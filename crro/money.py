"""
Money primitives: integer minor units (paise) for everything persisted.

Convention (see docs/FINANCIAL_SEMANTICS.md):
  * All persisted transaction amounts are integer paise (1 INR = 100 paise).
  * Conversion from external decimal representations uses Decimal and
    ROUND_HALF_UP exactly once, at the boundary. Floats are converted via
    their shortest repr (Decimal(str(x))) so 0.1 + 0.2 style artefacts do
    not leak into stored values.
  * Rupee values are produced only for presentation/reporting.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SUPPORTED_CURRENCIES = frozenset({"INR"})
PAISE_PER_RUPEE = 100
# Upper bound for a single payment in this prototype: ₹10 crore. Far below the
# BIGINT range; exists to reject obviously corrupt inputs (and overflow).
MAX_AMOUNT_PAISE = 10_000_000_000  # ₹100,000,000.00


class MoneyError(ValueError):
    """Raised for invalid amounts or currencies."""


def validate_currency(currency: str) -> str:
    if not isinstance(currency, str):
        raise MoneyError("currency must be a string")
    c = currency.strip().upper()
    if c not in SUPPORTED_CURRENCIES:
        raise MoneyError(f"unsupported currency {currency!r}; supported: {sorted(SUPPORTED_CURRENCIES)}")
    return c


def rupees_to_paise(value, *, allow_zero: bool = False) -> int:
    """Convert a rupee amount (int/str/Decimal/float) to integer paise.

    Rejects NaN/inf, negatives, values with more than 2 decimal places that are
    not exactly representable after rounding policy, and values above
    MAX_AMOUNT_PAISE. Rounds half-up to the nearest paisa.
    """
    if isinstance(value, bool) or value is None:
        raise MoneyError("amount is required and must be numeric")
    try:
        d = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise MoneyError(f"amount {value!r} is not a valid decimal number") from None
    if not d.is_finite():
        raise MoneyError("amount must be finite")
    paise = int((d * PAISE_PER_RUPEE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return _check_paise(paise, allow_zero=allow_zero)


def _check_paise(paise: int, *, allow_zero: bool) -> int:
    if paise < 0:
        raise MoneyError("amount must not be negative")
    if paise == 0 and not allow_zero:
        raise MoneyError("amount must be greater than zero")
    if paise > MAX_AMOUNT_PAISE:
        raise MoneyError(f"amount exceeds maximum supported value ({MAX_AMOUNT_PAISE} paise)")
    return paise


def validate_paise(value, *, allow_zero: bool = False) -> int:
    """Validate an integer paise value coming from a provider payload."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError("paise amount must be an integer")
    return _check_paise(value, allow_zero=allow_zero)


def paise_to_rupees(paise: int) -> Decimal:
    """Exact Decimal rupees for reports (never float)."""
    return (Decimal(int(paise)) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def percent_of_paise(paise: int, pct) -> int:
    """pct (e.g. 0.02) of an amount, rounded half-up to whole paise."""
    d = Decimal(int(paise)) * Decimal(str(pct))
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_inr(paise: int) -> str:
    """Indian digit grouping, e.g. 1234567.5 INR -> '₹12,34,567.50'."""
    neg = paise < 0
    r = paise_to_rupees(abs(int(paise)))
    whole, frac = f"{r:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if neg else ''}₹{whole}.{frac}"
