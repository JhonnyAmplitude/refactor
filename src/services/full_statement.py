import pandas as pd

from src.parsers.convertations import parse_security_movements
from src.parsers.header import parse_header
from src.parsers.fin_operations import parse_fin_operations
from src.parsers.stocks_bonds import parse_stock_bond_trades
from datetime import datetime
from typing import Any, Dict


def _make_fingerprint(op: Any) -> tuple:
    dt = getattr(op, "date", None)
    if isinstance(dt, datetime):
        dstr = dt.isoformat()
    else:
        dstr = str(dt)
    t = getattr(op, "operation_type", "") or ""
    s = getattr(op, "payment_sum", 0) or 0
    try:
        s_norm = round(float(s), 6)
    except Exception:
        s_norm = str(s)
    ticker = (getattr(op, "ticker", "") or "").strip()
    isin = (getattr(op, "isin", "") or "").strip()
    return ("fp", dstr, t, s_norm, ticker, isin)


def parse_full_statement(file_path: str) -> Dict:
    """
    Парсит заголовок, финансовые операции и сделки с ценными бумагами, конвертацией.
    """
    header = parse_header(file_path)

    fin_ops, fin_stats = parse_fin_operations(file_path)
    trade_ops, trade_stats = parse_stock_bond_trades(file_path)
    sec_ops, sec_stats = parse_security_movements(file_path)  # уже добавлено

    fin_stats = fin_stats or {}
    trade_stats = trade_stats or {}
    sec_stats = sec_stats or {}

    fin_count = fin_stats.get("parsed", len(fin_ops))
    trade_count = trade_stats.get("parsed", len(trade_ops))
    sec_count = sec_stats.get("parsed", len(sec_ops))

    meta = {
        "fin_ops_raw_count": fin_count,
        "trade_ops_raw_count": trade_count,
        "security_movements_raw_count": sec_count,
        "total_operations": len(fin_ops) + len(trade_ops) + len(sec_ops),
        "fin_stats": fin_stats,
        "trade_stats": trade_stats,
        "security_movements_stats": sec_stats,
        "unknown_fin_ops": fin_stats.get("unrecognized_names", []),
    }

    # Объединяем
    all_ops = fin_ops + trade_ops + sec_ops

    # === СОРТИРУЕМ ВСЁ ПО ДАТЕ ===
    def sort_key(op):
        d = op.date
        if isinstance(d, str):
            try:
                return pd.to_datetime(d, format="%Y-%m-%d %H:%M:%S", errors="coerce")
            except:
                return pd.NaT
        elif isinstance(d, datetime):
            return d
        else:
            return pd.NaT

    try:
        all_ops_sorted = sorted(all_ops, key=sort_key)
    except Exception as e:
        all_ops_sorted = all_ops  # fallback

    operations = [op.to_dict() for op in all_ops_sorted]

    return {
        **header,
        "operations": operations,
        "meta": meta,
    }