"""TEMP-BYPASS pay-first: shared currency comparisons for payment settlement."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def amount_to_cents(value):
    """Convert a KES amount to integer cents using consistent decimal rounding."""
    amount = Decimal(str(value if value is not None else 0))
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    try:
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Amount cannot be represented to cent precision.") from exc
    return int(rounded * 100)


def amounts_cover_due(amount_paid, amount_due):
    """Return whether a positive amount due is covered at cent precision."""
    paid_cents = amount_to_cents(amount_paid)
    due_cents = amount_to_cents(amount_due)
    return due_cents > 0 and paid_cents >= due_cents


def stk_amounts_match(pushed_amount, verified_amount):
    """Match the KES integer amount sent to STK against the provider verification."""
    return int(round(float(pushed_amount))) == int(round(float(verified_amount)))
