from __future__ import annotations
from typing import List, Any, Optional, Tuple, Dict
import re
import pandas as pd
from datetime import datetime as _dt, datetime
from src.OperationDTO import OperationDTO
from src.utils import logger, to_int_safe
from src.constants import resolve_special_operation, norm_str


ISIN_RE = re.compile(r"\b[A-Za-z]{2}[A-Za-z0-9]{9}\d\b", re.IGNORECASE)
REG_LONG_RE = re.compile(r"\b[0-9A-ZА-Я]{1,6}[-/][0-9A-ZА-Я\-\/]{3,}[0-9A-ZА-Я]?\b", re.IGNORECASE)

SECTION_RE = re.compile(r"движен\w* денежн\w* средств|движен\w* ценны\w* бумаг", re.IGNORECASE)

HEADER_KEYWORDS = {
    "instrument": ["наименование ценной бумаги", "isin", "регистрац", "№ гос. регистрац"],
    "datetime": ["дата операции", "дата"],
    "quantity": ["колич", "шт", "количество"],
    "type": ["тип операции", "вид операции", "операция"],
    "comment": ["коммент", "комментарий"],
}

def find_security_movements_block_start(df: pd.DataFrame) -> Optional[int]:
    """Ищем начало блока 'Движение ценных бумаг'"""
    for i, row in df.iterrows():
        text = " ".join(str(c) for c in row if str(c).strip())
        if not text:
            continue
        tnorm = norm_str(text)
        if "движение ценных бумаг" in tnorm:
            return i + 1
    return None

def find_header_row(df: pd.DataFrame, start_idx: int, lookahead: int = 5) -> Optional[int]:
    """Ищем строку заголовков"""
    n = len(df)
    end = min(n, start_idx + lookahead + 1)
    for i in range(start_idx, end):
        row = df.iloc[i]
        joined = " ".join(str(c).strip().lower() for c in row if str(c).strip())
        if not joined:
            continue
        if any(kw in joined for kw in ("наименование", "ценной бумаги", "дата", "количество")):
            return i
    return None

def map_header_indices(header_row: list) -> Dict[str, int]:
    """Сопоставляем ключевые слова с индексами колонок"""
    cols = {}
    for idx, cell in enumerate(header_row):
        if not str(cell).strip():
            continue
        low = norm_str(cell)
        for key, keywords in HEADER_KEYWORDS.items():
            if key in cols:
                continue
            for kw in keywords:
                if norm_str(kw) in low:
                    cols[key] = idx
                    break
    return cols

def parse_instrument_cell(cell: Any) -> Tuple[str, str]:
    """
    Из поля 'Наименование ценной бумаги, № гос. Регистрации, ISIN'
    возвращаем (isin, reg_number).
    """
    s = "" if cell is None else str(cell).strip()
    if not s:
        return "", ""

    m_isin = ISIN_RE.search(s)
    isin = m_isin.group(0).upper() if m_isin else ""

    parts = [p.strip() for p in re.split(r"[,\t;/]+", s) if p.strip()]

    reg_number = ""

    REG_PATTERN = re.compile(r"^[0-9]{1,2}[-/][0-9A-ZА-Я]{1,6}[-/][0-9]{3,6}[-/][A-ZА-Я0-9]$", re.IGNORECASE)

    for p in parts:
        if p.upper() == isin:
            continue
        cleaned = p.rstrip(" _.,;\"'()[]{}")
        if REG_PATTERN.match(cleaned):
            reg_number = cleaned
            break

    if not reg_number:
        REG_LONG_RE = re.compile(r"\b[0-9A-ZА-Я]{1,6}[-/][0-9A-ZА-Я\-\/]{3,}[0-9A-ZА-Я]?\b", re.IGNORECASE)
        for p in parts:
            if p.upper() == isin:
                continue
            cleaned_for_match = p.rstrip(" _.,;")
            m = REG_LONG_RE.search(cleaned_for_match)
            if m:
                candidate = m.group(0).strip(".,; _")
                if "-" in candidate and re.search(r"\d", candidate):
                    reg_number = candidate
                    break

    return isin, reg_number

def parse_security_movements_table(df: pd.DataFrame, header_idx: int, cols: Dict[str, int]) -> tuple[List[OperationDTO], dict]:
    results: List[OperationDTO] = []
    total_rows = 0
    parsed_rows = 0
    skipped_empty = 0
    skipped_no_date = 0
    skipped_no_qty = 0
    skipped_no_type = 0
    skipped_itogo = 0

    for r_idx in range(header_idx + 1, len(df)):
        total_rows += 1
        row = df.iloc[r_idx]
        cells = list(row)
        text_row = " ".join(str(c).strip() for c in cells if str(c).strip()).lower()

        if any(isinstance(c, str) and norm_str(c).startswith("итого") for c in cells):
            skipped_itogo += 1
            continue

        if all((c is None or (isinstance(c, str) and not c.strip())) for c in cells):
            logger.debug("Пустая строка -> конец блока движения ЦБ на строке %s", r_idx)
            break

        inst_idx = cols.get("instrument")
        curr_isin, curr_reg = "", ""
        if inst_idx is not None and inst_idx < len(cells):
            inst_cell = cells[inst_idx]
            if inst_cell is not None and str(inst_cell).strip():
                isin_val, regno = parse_instrument_cell(inst_cell)
                if isin_val:
                    curr_isin = isin_val
                if regno:
                    curr_reg = regno

        date_val = None
        dt_idx = cols.get("datetime")
        if dt_idx is not None and dt_idx < len(cells):
            raw_dt = cells[dt_idx]
            if raw_dt and str(raw_dt).strip():
                s = str(raw_dt).strip()
                s_fixed = s.replace(",", ".").replace("\u00A0", " ")
                _try_formats = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y")
                for _fmt in _try_formats:
                    try:
                        date_val = _dt.strptime(s_fixed, _fmt)
                        break
                    except Exception:
                        continue
                if date_val is None:
                    pd_dt = pd.to_datetime(s_fixed, dayfirst=True, errors="coerce")
                    if pd_dt is not None and not pd.isna(pd_dt):
                        date_val = pd_dt.to_pydatetime()
                if date_val is not None:
                    if isinstance(date_val, (_dt, datetime)):
                        date_val = date_val.strftime("%Y-%m-%d %H:%M:%S")

        if date_val is None:
            skipped_no_date += 1
            continue

        qty = 0
        q_idx = cols.get("quantity")
        if q_idx is not None and q_idx < len(cells):
            qty = to_int_safe(cells[q_idx])
        if qty == 0:
            skipped_no_qty += 1
            continue

        op_type_raw = ""
        t_idx = cols.get("type")
        if t_idx is not None and t_idx < len(cells):
            op_type_raw = str(cells[t_idx]).strip()

        op = resolve_special_operation(op_type_raw, qty, {"comment": "", "date": date_val})
        if not op:
            skipped_no_type += 1
            continue

        comment = ""
        comm_idx = cols.get("comment")
        if comm_idx is not None and comm_idx < len(cells):
            comment = str(cells[comm_idx]).strip()

        dto = OperationDTO(
            date=date_val,
            operation_type=op,
            payment_sum=0.0,
            currency="",
            isin=curr_isin,
            reg_number=curr_reg,
            quantity=qty,
            comment=comment,
            operation_id="",
            commission=0.0,
        )
        results.append(dto)
        parsed_rows += 1

    stats = {
        "total_rows": total_rows,
        "parsed": parsed_rows,
        "skipped_empty": skipped_empty,
        "skipped_no_date": skipped_no_date,
        "skipped_no_qty": skipped_no_qty,
        "skipped_no_type": skipped_no_type,
        "skipped_itogo": skipped_itogo,
    }

    logger.info(
        "Ввод/вывод активов: total_rows=%s parsed=%s skipped_empty=%s skipped_no_date=%s skipped_no_qty=%s skipped_no_type=%s skipped_itogo=%s",
        total_rows, parsed_rows, skipped_empty, skipped_no_date, skipped_no_qty, skipped_no_type, skipped_itogo
    )

    return results, stats

def parse_security_movements(file_path: str) -> tuple[List[OperationDTO], dict]:
    """Основная функция парсинга движения ценных бумаг"""
    df = pd.read_excel(file_path, header=None, dtype=object).fillna("")
    start_idx = find_security_movements_block_start(df)
    if start_idx is None:
        logger.info("Блок 'Движение ценных бумаг' не найден")
        return [], {}

    header_idx = find_header_row(df, start_idx)
    if header_idx is None:
        logger.warning("Строка заголовка не найдена для блока 'Движение ценных бумаг'")
        return [], {}

    cols = map_header_indices(df.iloc[header_idx].tolist())
    logger.debug("Обнаружены колонки для движения ЦБ: %s", cols)

    if not cols:
        logger.warning("Не удалось определить колонки для блока 'Движение ценных бумаг'")
        return [], {}

    results, stats = parse_security_movements_table(df, header_idx, cols)

    def key_fn(o: OperationDTO):
        d = o.date
        if isinstance(d, datetime):
            return d
        try:
            return pd.to_datetime(d, dayfirst=True, errors="coerce")
        except Exception:
            return pd.NaT

    results_sorted = sorted(results, key=key_fn)
    return results_sorted, stats