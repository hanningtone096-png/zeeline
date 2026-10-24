import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask

old_flask_env = os.environ.get("FLASK_ENV")
os.environ["FLASK_ENV"] = "development"
import payments
if old_flask_env is None:
    os.environ.pop("FLASK_ENV", None)
else:
    os.environ["FLASK_ENV"] = old_flask_env


class PaymentSettlementTests(unittest.TestCase):
    def test_partial_stk_amounts_accumulate_before_policy_activation(self):
        payments_by_reference = {
            "checkout-1": {
                "policy_no": "POL-TEST",
                "amount": 350,
                "status": "pending",
            },
            "checkout-2": {
                "policy_no": "POL-TEST",
                "amount": 650,
                "status": "pending",
            },
        }
        due = 1000
        activation = Mock(return_value=True)

        def fake_query(sql, params=(), *, fetchone=False, commit=False):
            if sql.startswith("SELECT amount FROM payments"):
                return {"amount": payments_by_reference[params[0]]["amount"]}
            if sql.startswith("UPDATE payments SET status='completed'"):
                payments_by_reference[params[0]]["status"] = "completed"
                return None
            if "SELECT COALESCE(SUM(amount),0) AS paid" in sql:
                total = sum(
                    payment["amount"] for payment in payments_by_reference.values()
                    if payment["policy_no"] == params[0] and payment["status"] == "completed"
                )
                return {"paid": total}
            if sql.startswith("SELECT total_payable FROM policies"):
                return {"total_payable": due}
            return None

        with patch.object(payments, "_query", side_effect=fake_query), \
                patch.object(payments, "activate_paid_policy_and_enqueue_dmvic",
                             side_effect=activation), \
                patch.object(payments, "_cache_delete_prefix"):
            first_activated = payments._settle_mpesa_payment(
                "checkout-1", "POL-TEST", source="test", reference="receipt-1",
                verified_amount=350,
            )
            second_activated = payments._settle_mpesa_payment(
                "checkout-2", "POL-TEST", source="test", reference="receipt-2",
                verified_amount=650,
            )

        self.assertFalse(first_activated)
        self.assertTrue(second_activated)
        self.assertEqual(payments_by_reference["checkout-1"]["amount"], 350)
        self.assertEqual(payments_by_reference["checkout-2"]["amount"], 650)
        self.assertEqual(
            sum(p["amount"] for p in payments_by_reference.values()
                if p["status"] == "completed"),
            due,
        )
        activation.assert_called_once()

    def test_agent_api_amount_is_ignored_and_full_balance_is_pushed(self):
        self._assert_stk_route_amount(
            role="agent",
            user_id=501,
            body={"policy_no": "POL-TEST", "phone": "0712345678", "amount": 10},
            expected_amount=1000,
            expected_override=False,
        )

    def test_admin_api_amount_is_pushed_and_audited(self):
        self._assert_stk_route_amount(
            role="admin",
            user_id=502,
            body={
                "policy_no": "POL-TEST",
                "phone": "0712345678",
                "amount": 400,
                "reason": "Client requested part payment",
            },
            expected_amount=400,
            expected_override=True,
        )

    def _assert_stk_route_amount(self, *, role, user_id, body, expected_amount,
                                 expected_override):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(payments.payments_bp)
        app.config["TESTING"] = True
        policy = {
            "policy_no": "POL-TEST",
            "agent_id": user_id if role == "agent" else 999,
            "status": "pending_payment",
            "total_payable": 1000,
        }
        writes = []
        calls = []

        def fake_query(sql, params=(), *, fetchone=False, commit=False):
            if sql.startswith("SELECT * FROM policies"):
                return policy
            if "SELECT COALESCE(SUM(amount),0) AS paid" in sql:
                return {"paid": 0}
            if sql.startswith("SELECT id FROM payments"):
                return None
            if sql.startswith("INSERT INTO payments"):
                writes.append((sql, params))
            if sql.startswith("INSERT INTO audit_log"):
                writes.append((sql, params))
            return None

        stk_push = Mock(return_value={
            "success": True,
            "checkout_request_id": "checkout-test",
            "customer_message": "Prompt sent",
        })
        send_email = Mock(return_value=True)
        with patch.object(payments, "_query", side_effect=fake_query), \
                patch.object(payments, "mpesa_stk_push", stk_push), \
                patch.object(payments, "_send_email", send_email), \
                patch.object(payments, "_company_email", "company@example.test"):
            client = app.test_client()
            with client.session_transaction() as session:
                session["user_id"] = user_id
                session["role"] = role
                session["status"] = "approved"
            response = client.post("/api/mpesa/stk", json=body)

        self.assertEqual(response.status_code, 200, response.get_json())
        stk_push.assert_called_once()
        self.assertEqual(stk_push.call_args.kwargs["amount"], expected_amount)
        payment_insert = next(write for write in writes
                              if write[0].startswith("INSERT INTO payments"))
        self.assertEqual(payment_insert[1][1], expected_amount)
        self.assertEqual(payment_insert[1][3], expected_override)
        if role == "admin":
            audit_insert = next(write for write in writes
                                if write[0].startswith("INSERT INTO audit_log"))
            self.assertEqual(audit_insert[1][0], user_id)
            self.assertEqual(audit_insert[1][1], "admin_mpesa_stk_push")
            self.assertTrue(send_email.called if expected_override else not send_email.called)
        else:
            self.assertFalse(any("underpayment" in write[0].lower() for write in writes))
            self.assertFalse(send_email.called)


if __name__ == "__main__":
    unittest.main()
