"""TEMP-BYPASS pay-first: regression tests for KES payment reconciliation."""

import unittest

from payment_amounts import amount_to_cents, amounts_cover_due, stk_amounts_match  # TEMP-BYPASS pay-first: regression coverage.


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


if __name__ == "__main__":
    unittest.main()
