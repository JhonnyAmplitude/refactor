from typing import Any, Callable, Dict, Optional
import re

_NBSP_PAT = re.compile(r"[\u00A0\u202F]")

def norm_str(s: Any) -> str:
    """
    Нормализует строку для сравнения:
    - None -> ""
    - заменяет NBSP на обычные пробелы
    - сводит множественные пробелы к одному
    - strip() и lower()
    """
    if s is None:
        return ""
    st = str(s)
    st = _NBSP_PAT.sub(" ", st)
    st = re.sub(r"\s+", " ", st)
    return st.strip().lower()


def _norm_key(s: str) -> str:
    """Короткая удобная обёртка для нормализации ключей в словарях."""
    return norm_str(s)


def to_float_safe(v: Any) -> Optional[float]:
    """Пытается превратить в float, возвращает None при ошибке."""
    if v is None or v == "":
        return None
    try:
        s = str(v)
        s = _NBSP_PAT.sub(" ", s)
        s = s.replace(",", ".").replace(" ", "")
        return float(s)
    except Exception:
        return None


def get_sign(value: Any) -> int:
    """
    Возвращает:
      -1 если число отрицательное;
       0 если равно нулю или не удалось распарсить;
      +1 если положительное.
    """
    v = to_float_safe(value)
    if v is None:
        return 0
    if v > 0:
        return 1
    elif v < 0:
        return -1
    return 0


VALID_OPERATIONS = {
    "Вознаграждение компании",
    "Вознаграждение Брокера",
    "Вознаграждение сторонних организаций",
    "Дивиденды",
    "Купонный доход",
    "Куп. дох.",
    "Погашение облигации",
    "Погашение ценных бумаг",
    "Приход ДС",
    "Зачисление денежных средств",
    "Перевод денежных средств",
    "Проценты по займам \"овернайт\"",
    "Проценты по займам \"овернайт ЦБ\"",
    "Частичное погашение облигации",
    "Вывод ДС",
    "НДФЛ"
}

SKIP_OPERATIONS = {
    "Сальдо расчётов по сделкам с ценными бумагами",
    "Сальдо расчетов по сделкам",
    "Внебиржевая сделка FX (22*)",
    "Займы \"овернайт\"",
    "НКД от операций",
    "Покупка/Продажа",
    "Покупка/Продажа (репо)",
    "Переводы между площадками",
    "Иные неторговые операции",
}


NORMALIZED_VALID_OPERATIONS = {norm_str(x) for x in VALID_OPERATIONS}
NORMALIZED_SKIP_OPERATIONS = {norm_str(x) for x in SKIP_OPERATIONS}


OPERATION_TYPE_MAP: Dict[str, str] = {
    _norm_key("Дивиденды"): "dividend",
    _norm_key("Купонный доход"): "coupon",
    _norm_key("Погашение ценных бумаг"): "repayment",
    _norm_key("Зачисление денежных средств"): "deposit",
    _norm_key("Списание денежных средств"): "withdrawal",
    _norm_key("Вознаграждение Брокера"): "commission",
    _norm_key("Вознаграждение сторонних организаций"): "commission",
    _norm_key("Перевод денежных средств"): "withdrawal",
}

SPECIAL_OPERATION_HANDLERS: Dict[str, Callable[[Any, dict], str]] = {
    _norm_key("Вознаграждение компании"): lambda amount, entry: "commission_refund" if get_sign(amount) > 0 else "commission",
    _norm_key("НДФЛ"): lambda amount, entry: "refund" if get_sign(amount) > 0 else "withholding",
    # Обработка перераспределения дохода — теперь не скипается, а корректно определяется по знаку суммы.
    _norm_key("Перераспределение дохода"): lambda amount, entry: "internal_transfer" if get_sign(amount) > 0 else "withdrawal",
    _norm_key("Конвертация ЦБ"): lambda amount, entry: "asset_receive" if get_sign(amount) > 0 else "asset_withdrawal",
}


def resolve_special_operation(op_raw: Any, amount: Any, entry: Optional[dict] = None) -> Optional[str]:
    """
    Попытка применить SPECIAL_OPERATION_HANDLERS:
    - делает нормализацию op_raw и ищет handler для любого ключа, который является подстрокой op_raw_norm
    - возвращает canonical operation string или None если не применимо
    """
    if op_raw is None:
        return None
    op_norm = norm_str(op_raw)
    for key, handler in SPECIAL_OPERATION_HANDLERS.items():
        if key in op_norm:
            try:
                return handler(amount, entry or {})
            except Exception:
                return None
    return None


CURRENCY_DICT = {
    "AED": "AED", "AMD": "AMD", "BYN": "BYN", "CHF": "CHF", "CNY": "CNY",
    "EUR": "EUR", "GBP": "GBP", "HKD": "HKD", "JPY": "JPY", "KGS": "KGS",
    "KZT": "KZT", "NOK": "NOK", "RUB": "RUB", "RUR": "RUB",
    "РУБЛЬ": "RUB", "РУБ": "RUB", "Рубль": "RUB", "Руб": "RUB",
    "SEK": "SEK", "TJS": "TJS", "TRY": "TRY", "USD": "USD", "UZS": "UZS",
    "XAG": "XAG", "XAU": "XAU", "ZAR": "ZAR", "₽": "RUB"
}
