"""TEMP-BYPASS pay-first: shared currency comparisons for payment settlement."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _decimal_amount(value):
    if isinstance(value, bool):
        raise ValueError("Amount must be a number.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Enter a valid amount.") from exc
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    return amount


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


def resolve_stk_amount(role, submitted_amount, outstanding_balance, reason=None,
                       *, min_amount, max_amount, policy_status=None):
    """Choose and validate the charge; only an authenticated admin can override."""
    if str(policy_status or "").strip().lower() in {"cancelled", "canceled"}:
        raise ValueError("M-Pesa payment is not available for a cancelled policy.")

    balance = _decimal_amount(outstanding_balance)
    if balance <= 0:
        raise ValueError("This policy has already been paid in full.")

    is_admin = role == "admin"
    amount = balance
    if is_admin and submitted_amount not in (None, ""):
        amount = _decimal_amount(submitted_amount)
        if amount != amount.to_integral_value():
            raise ValueError("Admin payment amount must be a whole shilling.")
        amount = amount.quantize(Decimal("1"))

    amount_to_send = int(amount) if is_admin and submitted_amount not in (None, "") else \
        max(1, int(round(float(amount))))
    if amount_to_send < min_amount:
        raise ValueError(f"The minimum M-Pesa payment is KES {min_amount}.")
    if amount_to_send > max_amount:
        raise ValueError(f"The maximum M-Pesa payment is KES {max_amount:,}.")
    if is_admin and amount > balance:
        raise ValueError("Admin payment amount cannot exceed the outstanding balance.")

    is_override = is_admin and amount != balance
    clean_reason = str(reason or "").strip()
    if is_override and len(clean_reason) < 5:
        raise ValueError("Enter a reason of at least 5 characters for changing the amount.")

    return amount_to_send if is_admin and submitted_amount not in (None, "") else float(balance), \
        is_override, clean_reason if is_override else None
