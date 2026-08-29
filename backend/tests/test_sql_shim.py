"""Tests for the hand-rolled SQL→Mongo shim in mongo_store.py.

Two layers:

1. TestEveryAppQuery — AST-parses the backend source, extracts every
   query()/_query() call's SQL literal (resolving f-strings), and runs each
   one through MongoStore.query() against an in-memory fake Mongo. Any new
   SELECT shape the translator doesn't support fails here instead of 500ing
   in production. This is the class of bug that broke /api/notifications
   and /api/commissions in the past.

2. Behaviour tests for specific translation semantics the smoke layer
   can't see (type coercion, interval filters, INSERT IGNORE, aliases).

Run:  python -m unittest discover -s backend/tests -v
"""
import ast
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
for p in (str(TESTS_DIR), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fake_mongo import FakeDatabase
from mongo_store import MongoStore

# ─────────────────────────────────────────────────────────────────────────────
# SQL extraction from source
# ─────────────────────────────────────────────────────────────────────────────

# Values substituted for f-string interpolations at the known dynamic
# call sites. where_clause mirrors list_agents' filter fragments; placeholders
# mirrors the renewal jobs' IN-list (REMINDER/NOTIFY_INTERVALS are 3-tuples).
FSTRING_SUBSTITUTIONS = {
    "where_clause": "u.role = 'agent' AND u.status = %s",
    "placeholders": "%s, %s, %s",
}
DEFAULT_SUBSTITUTION = "1"

QUERY_FUNC_NAMES = {"query", "_query"}
SOURCE_FILES = ["app.py", "payments.py", "ai_assistant.py", "premium_calc.py",
                "dmvic_mapping.py", "createadmin.py"]


def _resolve_sql(node):
    """Return the SQL text of a query() first argument, or None if dynamic."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                key = ast.unparse(value.value)
                parts.append(FSTRING_SUBSTITUTIONS.get(key, DEFAULT_SUBSTITUTION))
        return "".join(parts)
    return None


def _kwarg(call, name, default):
    for kw in call.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant):
                return kw.value.value
            return default
    return default


def _constant_params(call):
    """Extract params when the call passes a tuple/list of literals."""
    if len(call.args) < 2:
        return None
    arg = call.args[1]
    if isinstance(arg, (ast.Tuple, ast.List)) and all(
            isinstance(e, ast.Constant) for e in arg.elts):
        return [e.value for e in arg.elts]
    return None


def extract_query_calls():
    """Every query()/_query() call with statically knowable SQL, across the
    backend modules. Returns (file, lineno, sql, params, fetchone, commit)."""
    calls = []
    for fname in SOURCE_FILES:
        path = BACKEND_DIR / fname
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in QUERY_FUNC_NAMES):
                continue
            if not node.args:
                continue
            sql = _resolve_sql(node.args[0])
            if sql is None:
                continue  # built at runtime — covered by manual cases below
            literals = _constant_params(node) or []
            n = sql.count("%s")
            params = (list(literals) + [1] * n)[:n] if n else []
            calls.append((fname, node.lineno, sql, tuple(params),
                          bool(_kwarg(node, "fetchone", False)),
                          bool(_kwarg(node, "commit", False))))
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Test fixture: a store backed by the fake Mongo, seeded with representative docs
# ─────────────────────────────────────────────────────────────────────────────

def _days(n):
    return datetime.combine(date.today() + timedelta(days=n), datetime.min.time())


def seed(db):
    now = datetime(2026, 1, 15, 10, 0, 0)
    db.users.insert_many([
        {"id": 1, "full_name": "Admin User", "username": "admin",
         "email": "admin@zee.example", "role": "admin", "status": "approved",
         "commission_payout": "monthly", "created_at": now},
        {"id": 2, "full_name": "Ann Agent", "username": "ann",
         "email": "ann@zee.example", "role": "agent", "status": "approved",
         "commission_payout": "monthly", "flagged": 0, "flagged_reason": None,
         "flagged_at": None, "underpayment_attempts": 0, "created_at": now},
        {"id": 3, "full_name": "Pend Agent", "username": "pend",
         "email": "pend@zee.example", "role": "agent", "status": "pending",
         "flagged": 0, "flagged_reason": None, "flagged_at": None,
         "underpayment_attempts": 0, "created_at": now},
    ])
    db.clients.insert_many([
        {"id": 1, "agent_id": 2, "first_name": "Joy", "last_name": "Client",
         "phone": "0700000001", "email": "joy@client.example",
         "vehicle_reg": "KDA001", "created_at": now},
    ])
    db.quotations.insert_many([
        {"id": 1, "agent_id": 2, "company": "monarch", "make": "Toyota",
         "chassis_number": "CHAS001", "kra_pin": "A123", "engine_number": "E1",
         "vehicle_value": 1000000.0, "total_payable": 45000.0, "status": "accepted",
         "policy_holder_name": "Joy Client", "email": "joy@client.example",
         "phone": "0700000001", "business_type": "new", "installment_number": 1,
         "installment_total": 1, "commencing_date": _days(-200),
         "expiry_date": _days(200), "created_at": now},
    ])
    db.policies.insert_many([
        {"id": 1, "policy_no": "POL/001", "agent_id": 2, "client_id": 1,
         "quote_id": 1, "vehicle_reg": "KDA001", "type_of_cover": "comprehensive",
         "status": "active", "dmvic_status": "issued", "declared_at": None,
         "dmvic_issuance_request_id": "REQ001", "dmvic_error": None,
         "total_payable": 45000.0, "commencing_date": _days(-200),
         "expiry_date": _days(200), "created_at": now, "updated_at": now},
        # Renewal-window policies at the exact reminder/notify intervals.
        {"id": 2, "policy_no": "POL/002", "agent_id": 2, "client_id": 1,
         "quote_id": 1, "vehicle_reg": "KDA002", "type_of_cover": "third_party",
         "status": "active", "dmvic_status": "issued", "declared_at": None,
         "total_payable": 15000.0, "commencing_date": _days(-335),
         "expiry_date": _days(30), "created_at": now, "updated_at": now},
        {"id": 3, "policy_no": "POL/003", "agent_id": 2, "client_id": 1,
         "quote_id": 1, "vehicle_reg": "KDA003", "type_of_cover": "third_party",
         "status": "active", "dmvic_status": None, "declared_at": None,
         "total_payable": 15000.0, "commencing_date": _days(-362),
         "expiry_date": _days(3), "created_at": now, "updated_at": now},
    ])
    db.payments.insert_many([
        {"id": 1, "policy_no": "POL/001", "agent_id": 2, "amount": 45000.0,
         "status": "completed", "method": "mpesa", "reference": "REF001",
         "paid_at": now, "created_at": now},
    ])
    db.claims.insert_many([
        {"id": "CLM0001", "agent_id": 2, "claim_policy": "POL/001",
         "incident_date": _days(-10), "incident_type": "accident",
         "status": "pending", "created_at": now},
    ])
    db.notifications.insert_many([
        {"id": 1, "user_id": 2, "type": "policy_expiring", "title": "t",
         "message": "m", "link": "/renewals?policy=POL/002", "is_read": False,
         "created_at": datetime.now()},
    ])
    db.audit_log.insert_many([
        {"id": 1, "user_id": 2, "action": "login", "detail": None,
         "created_at": now},
    ])
    db.declarations.insert_many([
        {"id": 1, "policy_no": "POL/001", "created_by": 2, "email_sent": 0,
         "created_at": now},
    ])
    db.documents.insert_many([
        {"id": 1, "user_id": 2, "filename": "f.pdf", "filepath": "/tmp/f.pdf",
         "doc_type": "claim_evidence", "created_at": now},
    ])
    db.verification_codes.insert_many([
        {"id": 1, "user_id": 3, "purpose": "register", "code_hash": "x",
         "used": 0, "attempts": 0, "created_at": now, "expires_at": now},
    ])
    db.renewal_reminders.insert_many([
        {"id": 1, "policy_no": "POL/001", "recipient": "joy@client.example",
         "channel": "email", "interval_type": "30d", "sent_at": now,
         "created_at": now},
    ])
    db.commission_payouts.insert_many([
        {"id": 1, "agent_id": 2, "period_start": _days(-30), "period_end": _days(0),
         "amount": 1000.0, "status": "paid", "paid_at": now, "created_at": now},
    ])
    db.app_settings.insert_many([
        {"key": "commission_rate_percent", "value": "10", "created_at": now},
    ])
    # The seed bypassed _insert_doc, so the id counters know nothing about the
    # ids above — sync them or inserts would hand out colliding ids.
    for name, coll in list(db._collections.items()):
        ids = [d.get("id") for d in coll._docs if isinstance(d.get("id"), int)]
        if ids:
            db.counters.insert_one({"_id": name, "seq": max(ids)})


def make_store():
    store = MongoStore("mongodb://unused", "test")
    store.db = FakeDatabase()
    store._ready = True
    seed(store.db)
    return store


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: every app query must translate without raising
# ─────────────────────────────────────────────────────────────────────────────

class TestEveryAppQuery(unittest.TestCase):
    def test_all_query_calls_execute(self):
        calls = extract_query_calls()
        # Guard against the extractor silently finding nothing after a
        # refactor renames query().
        self.assertGreater(len(calls), 100,
                           f"Only {len(calls)} query calls extracted — "
                           "did query() get renamed?")
        failures = []
        for fname, lineno, sql, params, fetchone, commit in calls:
            store = make_store()
            try:
                store.query(sql, params, fetchone=fetchone, commit=commit)
            except Exception as exc:  # noqa: BLE001 — report, don't abort the run
                snippet = " ".join(sql.split())[:120]
                failures.append(f"{fname}:{lineno} {type(exc).__name__}: "
                                f"{exc}\n    {snippet!r}")
        self.assertEqual(
            failures, [],
            f"{len(failures)} of {len(calls)} app queries fail to translate:\n\n"
            + "\n".join(failures))


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: translation semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteSemantics(unittest.TestCase):
    def test_update_true_false_literals_become_bools(self):
        store = make_store()
        store.query("UPDATE notifications SET is_read=TRUE WHERE id=%s",
                    (1,), commit=True)
        row = store.db.notifications.find_one({"id": 1})
        self.assertIs(row["is_read"], True)

    def test_update_int_literal_is_coerced(self):
        store = make_store()
        store.query("UPDATE verification_codes SET used=1 WHERE id=%s",
                    (1,), commit=True)
        row = store.db.verification_codes.find_one({"id": 1})
        self.assertEqual(row["used"], 1)
        self.assertNotIsInstance(row["used"], str)

    def test_increment_assignment_uses_inc(self):
        store = make_store()
        store.query("UPDATE verification_codes SET attempts=attempts+1 "
                    "WHERE id=%s", (1,), commit=True)
        row = store.db.verification_codes.find_one({"id": 1})
        self.assertEqual(row["attempts"], 1)

    def test_insert_ignore_is_idempotent(self):
        store = make_store()
        sql = ("INSERT IGNORE INTO app_settings (`key`, `value`) "
               "VALUES ('commission_rate_percent', '10')")
        first = store.query(sql, commit=True)
        # Already seeded — must not duplicate or raise.
        self.assertIsNone(first)
        count = store.db.app_settings.count_documents(
            {"key": "commission_rate_percent"})
        self.assertEqual(count, 1)

    def test_insert_ignore_inserts_when_absent(self):
        store = make_store()
        sql = ("INSERT IGNORE INTO app_settings (`key`, `value`) "
               "VALUES ('theme', 'light')")
        store.query(sql, commit=True)
        row = store.db.app_settings.find_one({"key": "theme"})
        self.assertEqual(row["value"], "light")

    def test_ddl_statements_are_noops(self):
        store = make_store()
        self.assertIsNone(store.query(
            "CREATE TABLE commission_payouts (id INT)", commit=True))
        self.assertIsNone(store.query(
            "ALTER TABLE users ADD COLUMN note TEXT", commit=True))

    def test_insert_roundtrip(self):
        store = make_store()
        new_id = store.query(
            "INSERT INTO clients (agent_id, first_name, last_name, phone) "
            "VALUES (%s, %s, %s, %s)", (2, "New", "Client", "0700"), commit=True)
        row = store.query("SELECT * FROM clients WHERE id=%s", (new_id,),
                          fetchone=True)
        self.assertIsNotNone(row)
        self.assertEqual(row["first_name"], "New")


class TestReadSemantics(unittest.TestCase):
    def test_fetchone_returns_single_row(self):
        store = make_store()
        row = store.query("SELECT * FROM policies WHERE agent_id=%s", (2,),
                          fetchone=True)
        self.assertIsInstance(row, dict)
        self.assertEqual(row["agent_id"], 2)

    def test_renewal_intervals_filter_and_alias_days_left(self):
        """The daily renewal-reminder query must return only policies whose
        expiry falls on one of the reminder intervals, with days_left set."""
        store = make_store()
        placeholders = ",".join(["%s"] * 3)
        sql = f"""
            SELECT p.policy_no,
                   c.email AS client_email,
                   CONCAT(c.first_name,' ',c.last_name) AS client_name,
                   u.email AS agent_email,
                   u.full_name AS agent_name,
                   DATEDIFF(p.expiry_date, CURDATE()) AS days_left
            FROM   policies p
            LEFT JOIN clients c ON c.id = p.client_id
            LEFT JOIN users   u ON u.id = p.agent_id
            WHERE  p.status='active'
              AND  DATEDIFF(p.expiry_date, CURDATE()) IN ({placeholders})
        """
        rows = store.query(sql, (30, 14, 3))
        policy_nos = {r["policy_no"] for r in rows}
        self.assertEqual(policy_nos, {"POL/002", "POL/003"})
        for r in rows:
            self.assertIn(r["days_left"], (30, 3))

    def test_policy_join_rows_expose_client_and_agent_emails(self):
        """_send_renewal_reminder reads client_email/agent_email off the row —
        the shim must surface them from the joined users/clients documents."""
        store = make_store()
        rows = store.query("""
            SELECT p.policy_no, c.email AS client_email,
                   u.email AS agent_email
            FROM   policies p
            LEFT JOIN clients c ON c.id = p.client_id
            LEFT JOIN users   u ON u.id = p.agent_id
            WHERE  p.status='active'
        """)
        row = next(r for r in rows if r["policy_no"] == "POL/002")
        self.assertEqual(row["client_email"], "joy@client.example")
        self.assertEqual(row["agent_email"], "ann@zee.example")

    def test_expiry_notification_datediff_query_without_join(self):
        """The in-app expiry-notification query (no JOINs) must translate —
        it previously fell through to NotImplementedError."""
        store = make_store()
        placeholders = ",".join(["%s"] * 3)
        sql = f"""
            SELECT p.policy_no, p.agent_id,
                   DATEDIFF(p.expiry_date, CURDATE()) AS days_left
            FROM   policies p
            WHERE  p.status='active'
              AND  DATEDIFF(p.expiry_date, CURDATE()) IN ({placeholders})
        """
        rows = store.query(sql, (7, 3, 1))
        self.assertEqual({r["policy_no"] for r in rows}, {"POL/003"})
        self.assertEqual(rows[0]["days_left"], 3)

    def test_today_dedupe_clause_filters_to_today(self):
        """DATE(created_at)=CURDATE() must constrain to today's documents —
        the per-day notification dedupe depends on it."""
        store = make_store()
        db = store.db
        db.notifications.insert_many([
            {"id": 2, "user_id": 2, "type": "policy_expiring", "title": "t",
             "message": "m", "link": "/renewals?policy=POL/001",
             "is_read": False, "created_at": datetime.now()},
            {"id": 3, "user_id": 2, "type": "policy_expiring", "title": "t",
             "message": "m", "link": "/renewals?policy=POL/003",
             "is_read": False, "created_at": datetime.now() - timedelta(days=2)},
        ])
        rows = store.query("""SELECT id FROM notifications
                              WHERE type='policy_expiring' AND link=%s
                                AND DATE(created_at)=CURDATE()""",
                           ("/renewals?policy=POL/001",))
        self.assertEqual([r["id"] for r in rows], [2])

    def test_list_agents_search_with_ilike(self):
        store = make_store()
        sql = """
            SELECT u.id, u.full_name, u.username, u.email, u.status,
                   u.created_at
            FROM   users u
            LEFT JOIN quotations q ON q.agent_id = u.id
            WHERE  u.role = 'agent'
              AND  (u.full_name ILIKE %s OR u.username ILIKE %s
                    OR u.email ILIKE %s)
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """
        like = "%ann%"
        rows = store.query(sql, (like, like, like))
        self.assertEqual([r["username"] for r in rows], ["ann"])

    def test_declarations_query_with_optional_company(self):
        """Built dynamically in app.py (sql += ...), so extraction skips it."""
        sql = """
            SELECT p.policy_no, q.chassis_number, q.company,
                   u.full_name AS agent_name
            FROM   policies p
            LEFT JOIN quotations q ON q.id = p.quote_id
            LEFT JOIN users       u ON u.id = p.agent_id
            WHERE  p.dmvic_status = 'issued' AND p.declared_at IS NULL
        """
        store = make_store()
        base = store.query(sql + " ORDER BY q.company, p.dmvic_issued_at")
        self.assertEqual([r["policy_no"] for r in base], ["POL/001", "POL/002"])
        filtered = store.query(
            sql + " AND q.company = %s ORDER BY q.company, p.dmvic_issued_at",
            ("monarch",))
        self.assertEqual([r["policy_no"] for r in filtered],
                         ["POL/001", "POL/002"])


if __name__ == "__main__":
    unittest.main()