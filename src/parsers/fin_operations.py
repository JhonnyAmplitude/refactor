from __future__ import annotations
from typing import Any, List, Optional, Dict, Tuple
import re
import pandas as pd

from src.utils import logger, extract_date, to_int_safe, to_num_safe
from src.OperationDTO import OperationDTO
import src.constants

ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", re.IGNORECASE)
AMORT_RE = re.compile(r"(част\w{0,6}).*(погаш\w{0,6}).*(номин|номинал|обл)", re.IGNORECASE)
REG_LONG_RE = re.compile(
    r"\b[0-9][0-9A-ZА-Я]{0,7}[-/][0-9A-ZА-Я\-\/]*\d[0-9A-ZА-Я\-\/]*\b",
    re.IGNORECASE
)
REG_ALPHANUM_RE = re.compile(r"\b\d+[A-ZА-Я]{2,}\d*\b", re.IGNORECASE)

SECTION_RE_1 = re.compile(r"движен\w* денежн\w* средств", re.IGNORECASE)


HEADER_KEYWORDS = {
    "date": ["дата", "дата операции"],
    "type": ["операц", "вид операции", "тип операции", "наименование операции"],
    "sum": ["сумма", "сумма платежа"],
    "currency": ["валюта", "валютa", "вал."],
    "comment": ["коммент", "примечан"],
}

SECTION_END_KEYWORDS = ("итого", "всего", "баланс", "Внебиржевой рынок")


def find_section_start(df: pd.DataFrame) -> Optional[int]:
    """Ищем начало секции финансовых операций"""
    for idx, row in df.iterrows():
        joined = " ".join([str(c) for c in row if str(c).strip()]).lower()
        if not joined:
            continue
        if (SECTION_RE_1.search(joined)):
            logger.debug("Найдена строка начала секции: %s -> %s", idx, joined)
            return idx
    return None


def find_header_row(df: pd.DataFrame, start_idx: int, lookahead: int = 40) -> Optional[int]:
    """Ищем строку с заголовками таблицы"""
    nrows = len(df)
    if start_idx is None or start_idx < 0 or start_idx >= nrows:
        return None
    end_idx = min(start_idx + lookahead + 1, nrows)

    for i in range(start_idx + 1, end_idx):
        row = df.iloc[i]
        cells = [str(c).strip().lower() for c in row if str(c).strip()]

        if not cells:
            continue

        has_date = any("дата" in cell for cell in cells)
        has_sum = any("сумма" in cell for cell in cells)
        has_currency = any("валюта" in cell for cell in cells)
        has_operation = any("операц" in cell for cell in cells)
        has_type = any("тип" in cell for cell in cells)

        if has_date and (has_sum or has_currency or has_operation or has_type):
            logger.debug("Найдена строка заголовка на позиции %s: %s", i, " / ".join(cells))
            return i
    return None


def map_header_indices(header_row) -> Dict[str, int]:
    cols = {}
    for idx, cell in enumerate(header_row):
        if not str(cell).strip():
            continue
        low = str(cell).strip().lower()
        for key, keywords in HEADER_KEYWORDS.items():
            if any(k in low for k in keywords):
                if key not in cols:
                    cols[key] = idx
    return cols


def extract_isin_and_reg(comment: str) -> Tuple[Optional[str], Optional[str]]:
    if not comment:
        return None, None

    text = re.sub(r"\s+", " ", str(comment)).strip()

    m_isin = ISIN_RE.search(text)
    isin = m_isin.group(0).upper() if m_isin else None

    reg = None
    m = REG_LONG_RE.search(text)
    if m:
        reg = m.group(0).strip(".,; \"'()[]{}")
    else:
        m = REG_ALPHANUM_RE.search(text)
        if m:
            reg = m.group(0).strip(".,; \"'()[]{}")

    return isin, reg


def parse_fin_operations(file_path: str) -> tuple[List[OperationDTO], dict]:
    logger.info("Парсим финансовые операции из %s", file_path)
    try:
        df = pd.read_excel(file_path, header=None, dtype=object)
    except Exception as e:
        logger.error("Не удалось прочитать Excel %s: %s", file_path, e)
        return [], {"error": str(e)}

    df = df.fillna("")

    stats = {
        "total_rows": 0,
        "parsed": 0,
        "skipped_section_not_found": 0,
        "skipped_header_not_found": 0,
        "skipped_skiplist": 0,
        "skipped_zero_unknown": 0,
        "skipped_coupon_nonpositive": 0,
        "unrecognized_names": [],
        "amortizations": 0,
        "repayments": 0,
        "coupon_positive": 0,
        "coupon_negative_converted": 0,
        "reallocations_positive": 0,
    }

    _norm = getattr(src.constants, "norm_str", lambda x: str(x).strip().lower() if x else "")
    normalized_skip = { _norm(x) for x in getattr(src.constants, "SKIP_OPERATIONS", set()) }
    normalized_valid = { _norm(x) for x in getattr(src.constants, "VALID_OPERATIONS", set()) }
    normalized_op_map = { _norm(k): v for k, v in getattr(src.constants, "OPERATION_TYPE_MAP", {}).items() }
    special_handlers = getattr(src.constants, "SPECIAL_OPERATION_HANDLERS", {})
    normalized_special_map = { _norm(k): k for k in special_handlers.keys() }

    start_idx = find_section_start(df)
    if start_idx is None:
        logger.info("Секция финансовых операций не найдена.")
        stats["skipped_section_not_found"] = 1
        return [], stats

    header_idx = find_header_row(df, start_idx)
    if header_idx is None:
        logger.warning("Строка заголовка не найдена")
        stats["skipped_header_not_found"] = 1
        return [], stats

    header_row = df.iloc[header_idx]
    cols = map_header_indices(header_row)
    logger.debug("Обнаружены колонки: %s", cols)

    ops: List[OperationDTO] = []
    for i in range(header_idx + 1, len(df)):
        stats["total_rows"] += 1
        row = df.iloc[i]
        cells = [str(c).strip() for c in row if str(c).strip()]
        joined_low = " ".join(cells).lower()

        if not cells or any(k in joined_low for k in SECTION_END_KEYWORDS):
            break

        def g(col_key: str) -> Any:
            idx = cols.get(col_key)
            return row[idx] if idx is not None else None

        date_val = extract_date(g("date"))
        if not date_val:
            continue

        op_raw = g("type")
        op_raw_s = str(op_raw).strip() if op_raw else ""
        if not op_raw_s:
            comment_tmp = str(g("comment") or "").strip()
            if comment_tmp:
                stats["skipped_skiplist"] += 1
                stats["unrecognized_names"].append(comment_tmp)
                logger.warning("Пропускаем неизвестную операцию (не найден type) — %s (row=%s)", comment_tmp, i)
            continue

        payment_sum = to_num_safe(g("sum"))
        currency_raw = g("currency")
        currency = str(currency_raw).strip() if currency_raw else ""
        currency_normalized = src.constants.CURRENCY_DICT.get(currency.upper(), currency.upper() if currency else "")

        comment = str(g("comment") or "").strip()
        ticker = str(g("ticker") or "").strip() if "ticker" in cols else ""
        isin_col = str(g("isin") or "").strip()
        isin = isin_col or None
        reg_number = ""

        isin, reg_number = extract_isin_and_reg(comment)
        isin = isin or ""
        reg_number = reg_number or ""

        if "reg_number" in cols:
            reg_number = str(g("reg_number") or reg_number or "").strip()

        price = to_num_safe(g("price"))
        quantity = to_int_safe(g("quantity"))
        aci = to_num_safe(g("aci"))

        op_low = _norm(op_raw_s)
        c_norm = _norm(comment or "")

        op_type: Optional[str] = None

        if "погаш" in op_low:
            if payment_sum is None:
                pass
            elif payment_sum < 0:
                op_type = "withdrawal"
            elif payment_sum > 0:
                if AMORT_RE.search(comment):
                    op_type = "amortization"
                    stats["amortizations"] = stats.get("amortizations", 0) + 1
                else:
                    op_type = "repayment"
                    stats["repayments"] = stats.get("repayments", 0) + 1

        elif any(k in op_low for k in ("перераспредел", "перераспред", "распределение между")):
            if payment_sum is None:
                pass
            elif payment_sum < 0:
                op_type = "withdrawal"
            elif payment_sum > 0:
                stats["reallocations_positive"] = stats.get("reallocations_positive", 0) + 1
                logger.warning(
                    "Positive redistribution found (row=%s raw=%s comment=%s sum=%s) — logged but NOT returned to result",
                    i, op_raw_s, comment, payment_sum
                )
                continue

        elif any(k in op_low for k in ("купон", "купонный", "куп")):
            if payment_sum is None:
                pass
            elif payment_sum < 0:
                op_type = "withdrawal"
                stats["coupon_negative_converted"] = stats.get("coupon_negative_converted", 0) + 1
                logger.debug("Negative coupon converted to withdrawal (row=%s sum=%s comment=%s)", i, payment_sum, comment)
            elif payment_sum > 0:
                op_type = "coupon"
                stats["coupon_positive"] = stats.get("coupon_positive", 0) + 1

        if op_low in normalized_skip or any(sk in op_low for sk in normalized_skip):
            stats["skipped_skiplist"] += 1
            logger.debug("Пропускаем по skiplist: %s (row=%s)", op_raw_s, i)
            continue

        for norm_k, orig_k in normalized_special_map.items():
            if norm_k in op_low and not op_type:
                handler = special_handlers.get(orig_k)
                if callable(handler):
                    entry = {"date": date_val, "raw_type": op_raw_s, "sum": payment_sum, "comment": comment}
                    try:
                        op_type = handler(payment_sum, entry)
                    except Exception:
                        op_type = None
                break

        if not op_type and op_low in normalized_op_map:
            op_type = normalized_op_map[op_low]

        if not op_type:
            for k_norm, v in normalized_op_map.items():
                if k_norm in op_low:
                    op_type = v
                    break

        if not op_type:
            stats["skipped_skiplist"] += 1
            stats["unrecognized_names"].append(op_raw_s)
            logger.warning("Пропускаем неизвестную строку: %s (row=%s)", op_raw_s, i)
            continue

        dto = OperationDTO(
            date=date_val,
            operation_type=op_type,
            payment_sum=payment_sum,
            currency=currency_normalized,
            ticker=ticker,
            isin=(isin or ""),
            reg_number=(reg_number or ""),
            price=price,
            quantity=quantity,
            aci=aci,
            comment=comment,
            operation_id=str(g("operation_id") or "") or ""
        )
        ops.append(dto)
        stats["parsed"] += 1

    logger.info("Разобрано %s финансовых операций", len(ops))
    stats["unrecognized_names"] = list(dict.fromkeys(stats["unrecognized_names"]))
    return ops, stats
