"""TEMP-BYPASS pay-first: regression tests for KES payment reconciliation."""

import unittest

from payment_amounts import (
    amount_to_cents,
    amounts_cover_due,
    resolve_stk_amount,
    stk_amounts_match,
)  # TEMP-BYPASS pay-first: regression coverage.

ADMIN_MIN = 1
MPESA_MAX = 250000


def resolve(role, amount, balance=1000, reason=None, status="pending_payment"):
    return resolve_stk_amount(
        role,
        amount,
        balance,
        reason,
        min_amount=ADMIN_MIN,
        max_amount=MPESA_MAX,
        policy_status=status,
    )


class PaymentAmountsTests(unittest.TestCase):
    def test_exact_equal_amount_covers_due(self):
        self.assertTrue(amounts_cover_due("1250.00", "1250"))

    def test_float_sum_rounds_to_cents_before_comparison(self):
        self.assertTrue(amounts_cover_due(0.1 + 0.2, "0.30"))

    def test_one_cent_short_does_not_cover_due(self):
        self.assertFalse(amounts_cover_due("99.99", "100.00"))

    def test_rounds_half_cent_away_from_zero(self):
        self.assertEqual(amount_to_cents("10.005"), 1001)
        self.assertTrue(amounts_cover_due("10.005", "10.01"))

    def test_paid_amount_above_due_covers_due(self):
        self.assertTrue(amounts_cover_due("100.01", "100.00"))

    def test_stk_comparison_uses_existing_whole_kes_rounding(self):
        self.assertTrue(stk_amounts_match("100.49", "100"))
        self.assertTrue(stk_amounts_match("100.51", "101"))
        self.assertFalse(stk_amounts_match("100.49", "101"))

    def test_agent_submitted_amount_is_ignored_and_balance_is_charged(self):
        amount, overridden, reason = resolve("agent", 10, balance=1000)
        self.assertEqual(amount, 1000)
        self.assertFalse(overridden)
        self.assertIsNone(reason)

    def test_admin_valid_reduced_amount_is_accepted_with_reason(self):
        self.assertEqual(resolve("admin", 500, reason="Client requested part payment"),
                         (500, True, "Client requested part payment"))

    def test_admin_amount_above_balance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceed the outstanding balance"):
            resolve("admin", 1001, reason="Adjustment requested")

    def test_zero_and_negative_amounts_are_rejected(self):
        for amount in (0, -1):
            with self.subTest(amount=amount), self.assertRaisesRegex(ValueError, "minimum"):
                resolve("admin", amount)

    def test_decimal_amount_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "whole shilling"):
            resolve("admin", 100.5)

    def test_admin_changed_amount_requires_reason(self):
        with self.assertRaisesRegex(ValueError, "reason of at least 5"):
            resolve("admin", 500)
        with self.assertRaisesRegex(ValueError, "reason of at least 5"):
            resolve("admin", 500, reason="no")

    def test_fully_paid_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "already been paid in full"):
            resolve("admin", 100, balance=0)
        with self.assertRaisesRegex(ValueError, "already been paid in full"):
            resolve("agent", 100, balance=-1)

    def test_cancelled_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cancelled policy"):
            resolve("admin", 100, status="cancelled")

    def test_admin_amount_above_mpesa_limit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "maximum M-Pesa"):
            resolve("admin", MPESA_MAX + 1, balance=MPESA_MAX + 10,
                    reason="High balance installment")


if __name__ == "__main__":
    unittest.main()
