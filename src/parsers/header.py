from __future__ import annotations
import re
import pandas as pd
from typing import Optional, Any
from src.utils import logger, extract_date

PERIOD_RE = re.compile(
    r"за период с (\d{2}\.\d{2}\.\d{4}) по (\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)

SUBACCOUNT_RE = re.compile(r"№\s*субсчета[:\s]*([0-9A-Za-zА-Яа-я\-_\/]+)", re.IGNORECASE)


_1C_RE = re.compile(r'\b1C\d+\b', re.IGNORECASE)
ALNUM_MIX_RE = re.compile(r'\b(?=[0-9A-Za-zА-Яа-яЁё]*\d)(?=[0-9A-Za-zА-Яа-яЁё]*[A-Za-zА-Яа-яЁё])[0-9A-Za-zА-Яа-яЁё\-_\/]+\b', re.IGNORECASE)
SIMPLE_ALNUM_RE = re.compile(r'\b[0-9A-Za-zА-Яа-яЁё\-_\/]{2,}\b', re.IGNORECASE)
NUM_RE = re.compile(r'\d+')

def extract_account_id(raw: Any) -> str:
    """
    Надёжно извлекает account_id из произвольного текста.
    Возвращает строку в верхнем регистре (например '1C886').
    Не преобразует в int.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    m = _1C_RE.search(s)
    if m:
        return m.group(0).upper()

    m = ALNUM_MIX_RE.search(s)
    if m:
        return m.group(0).upper()

    m = SIMPLE_ALNUM_RE.search(s)
    if m:
        return m.group(0).upper()

    m = NUM_RE.search(s)
    if m:
        return m.group(0)

    return s


def parse_header(file_path: str) -> dict:
    """
    Читает верхнюю часть xlsx через pandas (header=None) и извлекает:
      - account_id (№ субсчета)
      - account_date_start (дата соглашения рядом с 'о предоставлении услуг')
      - date_start / date_end (период отчёта)
    """
    df = pd.read_excel(file_path, header=None)
    df = df.fillna("")

    account_id: Optional[str] = None
    account_date_start: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None

    for _, row in df.iterrows():
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        joined = " ".join(cells).strip()
        joined_low = joined.lower()

        if not (date_start and date_end):
            m = PERIOD_RE.search(joined_low)
            if m:
                date_start, date_end = m.group(1), m.group(2)
                logger.debug("Found period: %s - %s", date_start, date_end)

        if "о предоставлении услуг" in joined_low and not account_date_start:
            account_date_start = next(
                (d for cell in row for d in [extract_date(cell)] if d),
                None,
            )
            logger.debug("Found agreement date: %s", account_date_start)

        if not account_id:
            if "субсч" in joined_low or "субсчет" in joined_low or "субсчета" in joined_low:
                m2 = SUBACCOUNT_RE.search(joined)
                if m2:
                    raw_found = m2.group(1)
                    account_id_candidate = extract_account_id(raw_found)
                    if re.search(r'\d', account_id_candidate):
                        account_id = account_id_candidate
                        logger.debug("Found account id (by SUBACCOUNT_RE): %s (raw=%r)", account_id, raw_found)
                    else:
                        logger.debug("SUBACCOUNT_RE extracted candidate without digits: %r (raw=%r) — ignored", account_id_candidate, raw_found)
                else:
                    candidate = extract_account_id(joined)
                    if candidate and re.search(r'\d', candidate):
                        account_id = candidate
                        logger.debug("Found account id (from line with 'субсч'): %s (raw=%r)", account_id, joined)
                    else:
                        logger.debug("Line contains 'субсч' but no good candidate found (candidate=%r)", candidate)

            if not account_id:
                m2 = SUBACCOUNT_RE.search(joined)
                if m2:
                    raw_found = m2.group(1)
                    account_id_candidate = extract_account_id(raw_found)
                    if re.search(r'\d', account_id_candidate):
                        account_id = account_id_candidate
                        logger.debug("Found account id (by SUBACCOUNT_RE fallback): %s (raw=%r)", account_id, raw_found)
                    else:
                        logger.debug("SUBACCOUNT_RE fallback returned non-digit candidate: %r", account_id_candidate)

        if account_id and account_date_start and date_start and date_end:
            break

    result = {
        "account_id": account_id,
        "account_date_start": account_date_start,
        "date_start": date_start,
        "date_end": date_end,
    }
    logger.info("Header parsed: %s", result)
    return result
