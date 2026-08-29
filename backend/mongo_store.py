import re
import logging
from datetime import date, datetime, timedelta

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from werkzeug.security import generate_password_hash

log = logging.getLogger(__name__)


AUTO_ID_TABLES = {
    "users",
    "clients",
    "policies",
    "payments",
    "documents",
    "verification_codes",
    "audit_log",
    "reports",
    # The app reads and updates these tables by id (mark-notification-read,
    # commission payout records, reminder dedupe), so their inserts must get
    # one even though older documents may predate auto-id.
    "notifications",
    "commission_payouts",
    "renewal_reminders",
}

# Tables whose Mongo documents may already exist without an id field: their
# unique id index must be sparse or the build would fail on the duplicate
# nulls. All documents created from now on get an id via _insert_doc().
SPARSE_ID_TABLES = {
    "notifications",
    "commission_payouts",
    "renewal_reminders",
}

INT_FIELDS = {
    "id",
    "user_id",
    "agent_id",
    "client_id",
    "created_by",
    "attempts",
    "certificate_count",
    "underpayment_attempts",
    "seats",
    "pax",
    "installment_number",
    "installment_total",
}

FLOAT_FIELDS = {
    "amount",
    "vehicle_value",
    "tonnage",
    "base_premium",
    "levies_and_taxes",
    "total_payable",
    "premium",
    "excess_fee",
}

DATE_FIELDS = {
    "commencing_date",
    "original_commencing_date",
    "expiry_date",
    "incident_date",
    "start_date",
    "end_date",
    "created_at",
    "updated_at",
    "paid_at",
    "flagged_at",
    "declared_at",
    "dmvic_issued_at",
    "expires_at",
}


def _clean_sql(sql):
    return re.sub(r"\s+", " ", sql.strip()).strip()


def _lower_sql(sql):
    return _clean_sql(sql).lower()


def _split_csv(value):
    parts = []
    buf = []
    depth = 0
    quote = None
    for ch in value:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _coerce_field(field, value):
    if value is None:
        return None
    if field in INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field in FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if field in DATE_FIELDS and isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S"):
            try:
                # PyMongo supports datetime values but not datetime.date values.
                # Keep date-only form fields at midnight so they remain queryable
                # as dates without causing quotation creation to fail.
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
    return value


def _serialize(doc):
    if not doc:
        return doc
    out = dict(doc)
    out.pop("_id", None)
    return out


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _now():
    return datetime.utcnow()


class MongoStore:
    def __init__(self, uri, db_name, *, admin_email=None, admin_password=None):
        self.uri = uri
        self.db_name = db_name
        self.client = None
        self.db = None
        # SECURITY: there is NO hardcoded default admin password. A bootstrap
        # admin is only created when the operator explicitly provides both
        # ADMIN_EMAIL and ADMIN_PASSWORD via the environment; otherwise the
        # system refuses to seed a known-credential account. (The previous
        # fallback of "Admin@2024" was a backdoor baked into source control.)
        self.admin_email = admin_email or None
        self.admin_password = admin_password or None
        self._ready = False

    def _ensure_ready(self):
        if self._ready:
            return
        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=10000)
        self.db = self.client[self.db_name]
        self._ensure_indexes()
        self._ensure_default_admin()
        self._ready = True

    def _collection(self, table):
        if self.db is None:
            self._ensure_ready()
        return self.db[table]

    def _ensure_indexes(self):
        self.db.counters.create_index([("_id", ASCENDING)])
        for table in AUTO_ID_TABLES:
            self._collection(table).create_index(
                [("id", ASCENDING)],
                unique=True,
                sparse=(table in SPARSE_ID_TABLES),
            )
        self.db.users.create_index([("username", ASCENDING)], unique=True)
        self.db.users.create_index([("email", ASCENDING)], sparse=True)
        self.db.verification_codes.create_index([
            ("user_id", ASCENDING), ("purpose", ASCENDING), ("created_at", DESCENDING),
        ])
        # OTP writes used to omit this field, while reads correctly filtered on
        # used=0. Backfill those documents once on startup and make the default
        # explicit so registration and password-reset codes remain readable.
        self.db.verification_codes.update_many(
            {"used": {"$exists": False}}, {"$set": {"used": 0}}
        )
        self.db.clients.create_index([("agent_id", ASCENDING), ("phone", ASCENDING)])
        self.db.quotations.create_index([("id", ASCENDING)], unique=True)
        self.db.quotations.create_index([("agent_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.quotations.create_index([("parent_policy_no", ASCENDING)], sparse=True)
        self.db.policies.create_index([("policy_no", ASCENDING)], unique=True)
        self.db.policies.create_index([("agent_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.policies.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        self.db.policies.create_index([
            ("vehicle_reg", ASCENDING), ("installment_plan", ASCENDING), ("created_at", DESCENDING),
        ])
        self.db.policies.create_index([("parent_policy_no", ASCENDING)], sparse=True)
        # MongoDB documents are schema-flexible, so the DMVIC issuance-request
        # field needs no ALTER TABLE equivalent. This sparse index is the
        # Mongo counterpart of the SQL migration and keeps the review queue fast.
        self.db.policies.create_index([("dmvic_issuance_request_id", ASCENDING)], sparse=True)
        self.db.payments.create_index([("policy_no", ASCENDING)])
        self.db.payments.create_index([("reference", ASCENDING)])
        self.db.claims.create_index([("id", ASCENDING)], unique=True)
        # MongoDB has no SQL migration step. Seed Monarch's independent
        # policy-number counters once, without overwriting live sequences.
        for policy_class, last_seq in {"private": 533143, "commercial": 12717}.items():
            self.db.monarch_policy_sequences.update_one(
                {"_id": policy_class},
                {"$setOnInsert": {"policy_class": policy_class, "last_seq": last_seq}},
                upsert=True,
            )

    def _ensure_default_admin(self):
        if self.db.users.find_one({"role": "admin"}):
            return
        # Only seed a bootstrap admin when the operator explicitly configured
        # both credentials. Never auto-create an admin with a known/hardcoded
        # password — that would let anyone who reads this source log in as admin.
        if not self.admin_email or not self.admin_password:
            log.error(
                "No admin user exists and ADMIN_EMAIL/ADMIN_PASSWORD are not set. "
                "Skipping bootstrap admin creation. Create one via createadmin.py "
                "(or set ADMIN_EMAIL and ADMIN_PASSWORD) before deploying."
            )
            return
        self._insert_doc("users", {
            "full_name": "System Administrator",
            "username": "admin",
            "email": self.admin_email,
            "password_hash": generate_password_hash(self.admin_password),
            "role": "admin",
            "status": "approved",
        })

    def _next_id(self, table):
        doc = self.db.counters.find_one_and_update(
            {"_id": table},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    def _defaults(self, table, doc):
        now = _now()
        doc.setdefault("created_at", now)
        if table in {"users", "clients", "policies", "claims"}:
            doc.setdefault("updated_at", now)
        if table == "users":
            doc.setdefault("role", "agent")
            doc.setdefault("status", "pending")
            doc.setdefault("flagged", 0)
            doc.setdefault("flagged_reason", None)
            doc.setdefault("flagged_at", None)
            doc.setdefault("underpayment_attempts", 0)
        if table == "quotations":
            doc.setdefault("status", "pending")
            doc.setdefault("business_type", "new")
            doc.setdefault("parent_policy_no", None)
            doc.setdefault("original_commencing_date", None)
            doc.setdefault("installment_plan", None)
            doc.setdefault("installment_number", 1)
            doc.setdefault("installment_total", 1)
        if table == "policies":
            # New policies are not cover until payment has been verified.
            # MongoDB is schema-flexible, so this is the migration-equivalent
            # default for documents created without an explicit status.
            doc.setdefault("status", "pending_payment")
            doc.setdefault("business_type", "new")
            doc.setdefault("parent_policy_no", None)
            doc.setdefault("original_commencing_date", doc.get("commencing_date"))
            doc.setdefault("installment_plan", None)
            doc.setdefault("installment_number", 1)
            doc.setdefault("installment_total", 1)
        if table == "payments":
            doc.setdefault("status", "pending")
            doc.setdefault("method", "manual")
            doc.setdefault("paid_at", None)
        if table == "claims":
            doc.setdefault("status", "pending")
        if table == "declarations":
            doc.setdefault("email_sent", 0)
        if table == "verification_codes":
            doc.setdefault("used", 0)
        return doc

    def activate_policy_after_payment(self, policy_no):
        """Atomically activate one newly paid policy and claim its DMVIC job.

        The payment verifier and webhook can report the same settlement.  The
        pending_payment predicate makes this transition idempotent: exactly one
        caller receives the pre-transition policy document and may enqueue
        certificate issuance.
        """
        self._ensure_ready()
        previous = self.db.policies.find_one_and_update(
            {"policy_no": policy_no, "status": "pending_payment"},
            {
                "$set": {
                    "status": "active",
                    "dmvic_status": "queued",
                    "dmvic_error": None,
                    "updated_at": _now(),
                }
            },
            return_document=ReturnDocument.BEFORE,
        )
        return _serialize(previous)

    def next_monarch_policy_sequence(self, policy_class):
        """Return the next Monarch class-specific policy sequence atomically."""
        self._ensure_ready()
        sequence = self.db.monarch_policy_sequences.find_one_and_update(
            {"_id": policy_class},
            {"$inc": {"last_seq": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not sequence or "last_seq" not in sequence:
            raise RuntimeError(f"Monarch sequence is unavailable for {policy_class}")
        return int(sequence["last_seq"])

    def find_extendable_installment_policy(self, vehicle_reg):
        """Find the newest active installment policy with a remaining stage.

        This is deliberately a Mongo-native lookup instead of sending a SQL
        join through the compatibility layer. The application still performs
        owner authorization before returning the document to the browser.
        """
        self._ensure_ready()
        rows = self._find_many(
            "policies",
            {
                "vehicle_reg": str(vehicle_reg or "").strip().upper(),
                "status": "active",
                "installment_plan": {"$in": ["inst_2", "inst_3"]},
            },
            sort=[("created_at", DESCENDING)],
        )
        for row in rows:
            try:
                if int(row.get("installment_number") or 1) < int(row.get("installment_total") or 1):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def _insert_doc(self, table, doc):
        doc = {k: _coerce_field(k, v) for k, v in doc.items()}
        if table in AUTO_ID_TABLES and "id" not in doc:
            doc["id"] = self._next_id(table)
        doc = self._defaults(table, doc)
        self._collection(table).insert_one(doc)
        return doc.get("id")

    def _find_one(self, table, filter_doc, projection=None):
        return _serialize(self._collection(table).find_one(filter_doc, projection))

    def _find_many(self, table, filter_doc=None, *, sort=None, limit=0):
        cur = self._collection(table).find(filter_doc or {})
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(limit)
        return [_serialize(d) for d in cur]

    def query(self, sql, params=(), fetchone=False, commit=False):
        self._ensure_ready()
        sql_clean = _clean_sql(sql)
        sql_l = sql_clean.lower()
        params = tuple(params or ())

        if commit:
            return self._write(sql_clean, sql_l, params)

        rows = self._read(sql_clean, sql_l, params)
        if fetchone:
            return rows[0] if rows else None
        return rows

    def _write(self, sql, sql_l, params):
        if sql_l.startswith("insert into ") or sql_l.startswith("insert ignore into "):
            return self._insert_sql(
                sql, params, ignore=sql_l.startswith("insert ignore into "))
        if sql_l.startswith("update "):
            return self._update_sql(sql, sql_l, params)
        if sql_l.startswith("delete from "):
            return self._delete_sql(sql_l, params)
        if sql_l.startswith(("create ", "alter ", "drop ")):
            # Mongo documents are schema-flexible: the legacy DDL statements
            # in ensure_schema() describe SQL tables that don't exist here.
            # Treat them as intentional no-ops instead of erroring on boot.
            return None
        raise NotImplementedError(f"Unsupported Mongo write query: {sql}")

    def _insert_sql(self, sql, params, *, ignore=False):
        m = re.search(r"insert\s+(?:ignore\s+)?into\s+(\w+)\s*\((.*?)\)\s*values\s*\((.*)\)", sql, re.I)
        if not m:
            raise NotImplementedError(f"Unsupported INSERT: {sql}")
        table = m.group(1).lower()
        columns = [c.strip().strip("`") for c in _split_csv(m.group(2))]
        values = _split_csv(m.group(3))
        param_index = 0
        doc = {}
        for col, token in zip(columns, values):
            token_l = token.lower()
            if token == "%s":
                value = params[param_index]
                param_index += 1
            elif token_l == "now()":
                value = _now()
            elif token_l == "null":
                value = None
            elif token_l in {"true", "false"}:
                value = token_l == "true"
            elif token.startswith("'") and token.endswith("'"):
                value = token[1:-1]
            else:
                try:
                    value = int(token)
                except ValueError:
                    try:
                        value = float(token)
                    except ValueError:
                        value = token
            doc[col] = _coerce_field(col, value)
        if ignore:
            # INSERT IGNORE semantics: skip when a row with the same natural
            # key already exists. The key is the literal (non-%s) assignments;
            # today that is app_settings.key seeding.
            exists_flt = ({"key": doc["key"]} if "key" in doc
                          else {k: v for k, v in doc.items()
                                if not isinstance(v, (int, float, bool, datetime))})
            if exists_flt and self._collection(table).find_one(exists_flt):
                return None
        return self._insert_doc(table, doc)

    def _where_filter(self, where, params):
        where_l = where.lower().strip()
        if " in " in where_l:
            m = re.search(r"where\s+(\w+)\s+in", where_l)
            if m:
                field = m.group(1)
                return {field: {"$in": [_coerce_field(field, p) for p in params]}}
            # " in " appeared inside a function call such as DATEDIFF(...) IN
            # (...) rather than a simple "field IN (...)" — fall through to
            # AND-clause parsing instead of crashing on the failed match.
        clauses = [c.strip() for c in re.split(r"\s+and\s+", where, flags=re.I)]
        filter_doc = {}
        param_index = 0
        for clause in clauses:
            if "date(created_at)" in clause and "curdate()" in clause:
                # DATE(created_at)=CURDATE() — the per-day notification dedupe.
                # Stored created_at values are datetimes, so constrain to
                # [today midnight, tomorrow midnight).
                midnight = datetime.combine(date.today(), datetime.min.time())
                filter_doc["created_at"] = {
                    "$gte": midnight, "$lt": midnight + timedelta(days=1),
                }
                continue
            m = re.search(r"([\w.]+)\s*=\s*(%s|'[^']*'|\d+)", clause, re.I)
            if not m:
                continue
            field = m.group(1).split(".")[-1]
            token = m.group(2)
            if token == "%s":
                value = params[param_index]
                param_index += 1
            elif token.startswith("'"):
                value = token.strip("'")
            else:
                value = int(token)
            filter_doc[field] = _coerce_field(field, value)
        return filter_doc

    def _update_sql(self, sql, sql_l, params):
        m = re.search(r"update\s+(\w+)\s+set\s+(.*?)\s+where\s+(.*)", sql, re.I)
        where = None
        if m:
            table, assignments, where = m.group(1).lower(), m.group(2), m.group(3)
        else:
            # Admin "mark all as read" style updates carry no WHERE clause and
            # must apply to every row rather than raise.
            m = re.search(r"update\s+(\w+)\s+set\s+(.*)", sql, re.I)
            if not m:
                raise NotImplementedError(f"Unsupported UPDATE: {sql}")
            table, assignments = m.group(1).lower(), m.group(2)
        assignments = _split_csv(assignments)
        set_doc = {}
        inc_doc = {}
        param_index = 0
        for assignment in assignments:
            left, right = [p.strip() for p in assignment.split("=", 1)]
            field = left.split(".")[-1].strip("`")
            right_l = right.lower()
            if right == "%s":
                value = params[param_index]
                param_index += 1
                set_doc[field] = _coerce_field(field, value)
            elif right_l == "now()":
                set_doc[field] = _now()
            elif right_l == "null":
                set_doc[field] = None
            elif right_l in {"true", "false"}:
                # Unquoted SQL boolean literal (is_read=TRUE), not a string.
                set_doc[field] = right_l == "true"
            elif re.match(rf"{re.escape(field)}\s*\+\s*1", right_l):
                inc_doc[field] = 1
            elif right.startswith("'") and right.endswith("'"):
                set_doc[field] = right[1:-1]
            else:
                # Unquoted numeric literals (used=1, flagged=0) must be
                # stored as numbers so equality filters keep matching.
                try:
                    value = int(right)
                except ValueError:
                    try:
                        value = float(right)
                    except ValueError:
                        value = right
                set_doc[field] = _coerce_field(field, value)

        where_params = params[param_index:]
        filter_doc = self._where_filter("WHERE " + where, where_params) if where else {}
        update_doc = {}
        if set_doc:
            update_doc["$set"] = set_doc
        if inc_doc:
            update_doc["$inc"] = inc_doc
        if table in {"users", "clients", "policies", "claims"}:
            update_doc.setdefault("$set", {})["updated_at"] = _now()
        result = self._collection(table).update_many(filter_doc, update_doc)
        return result.modified_count

    def _delete_sql(self, sql_l, params):
        if "payments where policy_no in" in sql_l:
            policy_nos = [p["policy_no"] for p in self._find_many("policies", {"agent_id": int(params[0])})]
            return self.db.payments.delete_many({"policy_no": {"$in": policy_nos}}).deleted_count
        m = re.search(r"delete\s+from\s+(\w+)\s+where\s+(.*)", sql_l, re.I)
        if not m:
            raise NotImplementedError(f"Unsupported DELETE: {sql_l}")
        table = m.group(1).lower()
        filter_doc = self._where_filter("WHERE " + m.group(2), params)
        return self._collection(table).delete_many(filter_doc).deleted_count

    def _read(self, sql, sql_l, params):
        if "from users u" in sql_l and (
            "where u.role = 'agent'" in sql_l or "where u.role='agent'" in sql_l
        ):
            return self._agent_summary(
                params,
                period_start=("date(q.created_at)" in sql_l),
                status="u.status = %s" in sql_l,
                q="ilike" in sql_l,
            )
        if "from clients c left join users" in sql_l:
            return self._clients_with_agents(params if "where c.agent_id" in sql_l else ())
        if "from claims c left join users" in sql_l:
            return self._claims_with_agents(params if "where c.agent_id" in sql_l else ())
        if "from quotations q left join users" in sql_l:
            return self._quotations_with_agents(params, period_start=("date(q.created_at)" in sql_l))
        if "from policies p" in sql_l and " left join " in sql_l:
            return self._policies_join(sql_l, params)
        if "from declarations d left join users" in sql_l:
            return self._declarations_history()
        if "from audit_log a left join users" in sql_l:
            return self._audit_log()
        # Client-360 claims: claims joined to policies by claim_policy and
        # filtered by the policy's client_id.
        if "from claims cl join policies" in sql_l:
            return self._claims_by_client(params)
        # DATEDIFF expiry queries without JOINs (the expiry-notification job)
        # need the interval post-filter that a plain equality filter can't
        # express; joined variants go through _policies_join above.
        if "datediff" in sql_l and "from policies" in sql_l:
            return self._datediff_postfilter(self._select_simple(sql_l, params), sql_l, params)

        if "count(*) as total" in sql_l:
            return [self._count_or_sum(sql_l, params)]
        if "coalesce(sum(" in sql_l:
            return [self._count_or_sum(sql_l, params)]
        if sql_l.startswith("select * from "):
            return self._select_simple(sql_l, params)
        if re.match(r"select [\w,\s.]+ from \w+(?:\s+\w+)?\s+where", sql_l):
            return self._select_simple(sql_l, params)

        if "from verification_codes" in sql_l:
            return self._select_simple(sql_l, params)
        if "from payments" in sql_l:
            return self._select_simple(sql_l, params)
        if "from notifications" in sql_l:
            return self._select_simple(sql_l, params)
        if "from declarations where" in sql_l:
            return self._select_simple(sql_l, params)
        raise NotImplementedError(f"Unsupported Mongo read query: {sql}")

    def _select_simple(self, sql_l, params):
        m = re.search(r"from\s+(\w+)", sql_l)
        if not m:
            raise NotImplementedError(f"Unsupported SELECT: {sql_l}")
        table = m.group(1)
        filter_doc = {}
        if " where " in sql_l:
            where = sql_l.split(" where ", 1)[1]
            where = where.split(" order by ", 1)[0].split(" limit ", 1)[0]
            if "username=%s or email=%s" in where:
                filter_doc = {"$or": [{"username": params[0]}, {"email": params[1]}]}
            else:
                filter_doc = self._where_filter("WHERE " + where, params)
        sort = None
        m_order = re.search(r"order by (.+?)(?:\s+limit\s+\d+)?\s*$", sql_l)
        if m_order:
            specs = []
            for spec in m_order.group(1).split(","):
                ms = re.fullmatch(r"([\w.]+)(?:\s+(asc|desc))?", spec.strip())
                if not ms:
                    # ORDER BY COALESCE(...) etc. — leave unsorted rather
                    # than guessing.
                    specs = None
                    break
                specs.append((ms.group(1).split(".")[-1],
                              DESCENDING if ms.group(2) == "desc" else ASCENDING))
            if specs:
                sort = specs
        m = re.search(r"limit (\d+)", sql_l)
        limit = int(m.group(1)) if m else 0
        rows = self._find_many(table, filter_doc, sort=sort, limit=limit)
        return rows

    def _count_or_sum(self, sql_l, params):
        table = re.search(r"from\s+(\w+)", sql_l).group(1)
        filter_doc = {}
        if " where " in sql_l:
            where = sql_l.split(" where ", 1)[1]
            filter_doc = self._where_filter("WHERE " + where, params)
        if "count(*) as total" in sql_l:
            return {"total": self._collection(table).count_documents(filter_doc)}
        field = re.search(r"sum\((\w+)\)", sql_l).group(1)
        total = sum(float(doc.get(field) or 0) for doc in self._collection(table).find(filter_doc, {field: 1}))
        alias_match = re.search(r"\bas\s+(\w+)", sql_l)
        return {(alias_match.group(1) if alias_match else "total"): total}

    def _with_agent(self, doc, *, prefix="agent"):
        out = dict(doc)
        user = self._find_one("users", {"id": out.get("agent_id")})
        if user:
            out[f"{prefix}_name"] = user.get("full_name")
            out[f"{prefix}_email"] = user.get("email")
        return out

    def _quotations_with_agents(self, params, *, period_start=False):
        filter_doc = {}
        if params and not period_start:
            filter_doc["agent_id"] = int(params[0])
        rows = self._find_many("quotations", filter_doc, sort=[("created_at", DESCENDING)], limit=200 if not period_start else 0)
        if period_start and params:
            start = _as_date(params[0])
            if start is not None:
                rows = [r for r in rows if _as_date(r.get("created_at")) and _as_date(r.get("created_at")) >= start]
        return [self._with_agent(r) for r in rows]

    def _claims_by_client(self, params):
        """Claims linked to a client through the policy on each claim."""
        policy_nos = [p["policy_no"] for p in
                      self._find_many("policies", {"client_id": int(params[0])})]
        if not policy_nos:
            return []
        return self._find_many("claims", {"claim_policy": {"$in": policy_nos}},
                               sort=[("created_at", DESCENDING)])

    def _datediff_postfilter(self, rows, sql_l, params):
        """Apply DATEDIFF(expiry_date, CURDATE()) window/interval filters in
        Python and attach days_remaining/days_left to each row.

        The renewal-reminder and expiry-notification jobs filter on
        DATEDIFF(...) IN (30, 14, 3) style interval lists, which no equality
        filter can express; the renewals page filters on DATEDIFF(...) <= 30.
        """
        today = date.today()
        intervals = None
        in_m = re.search(r"\)\s*in\s*\(((?:\s*%s\s*,)*\s*%s)\)", sql_l)
        if in_m:
            count = in_m.group(1).count("%s")
            try:
                intervals = {int(p) for p in params[-count:]}
            except (TypeError, ValueError):
                intervals = None
        window_30 = "datediff(p.expiry_date, curdate()) <= 30" in sql_l
        out = []
        for row in rows:
            expiry = _as_date(row.get("expiry_date"))
            days = (expiry - today).days if expiry else None
            row["days_remaining"] = days
            row["days_left"] = days
            if window_30 and (days is None or days > 30):
                continue
            if intervals is not None and days not in intervals:
                continue
            out.append(row)
        return sorted(out, key=lambda r: _as_date(r.get("expiry_date")) or date.max)

    def _clients_with_agents(self, params):
        filter_doc = {"agent_id": int(params[0])} if params else {}
        rows = self._find_many("clients", filter_doc, sort=[("created_at", DESCENDING)])
        return [self._with_agent(r) for r in rows]

    def _claims_with_agents(self, params):
        filter_doc = {"agent_id": int(params[0])} if params else {}
        rows = self._find_many("claims", filter_doc, sort=[("created_at", DESCENDING)])
        return [self._with_agent(r) for r in rows]

    def _agent_summary(self, params, *, period_start=False, status=False, q=False):
        start = _as_date(params[0]) if period_start and params else None
        # Optional filters built by list_agents() in app.py. Params arrive in
        # the order app.py builds them: [status]?, then three identical LIKE
        # patterns for (full_name / username / email). The reports query
        # (period_start) never combines with these, so there is no overlap.
        user_filter = {"role": "agent"}
        needle = None
        idx = 0
        if status:
            user_filter["status"] = params[idx]
            idx += 1
        if q:
            needle = str(params[idx]).replace("%", "").lower()
        agents = self._find_many("users", user_filter, sort=[("created_at", DESCENDING)])
        if needle:
            agents = [a for a in agents
                      if needle in str(a.get("full_name") or "").lower()
                      or needle in str(a.get("username") or "").lower()
                      or needle in str(a.get("email") or "").lower()]
        rows = []
        for agent in agents:
            aid = agent["id"]
            qrows = self._find_many("quotations", {"agent_id": aid})
            prows = self._find_many("policies", {"agent_id": aid})
            clients = self._find_many("clients", {"agent_id": aid})
            if start:
                qrows = [r for r in qrows if _as_date(r.get("created_at")) and _as_date(r.get("created_at")) >= start]
                prows = [r for r in prows if _as_date(r.get("created_at")) and _as_date(r.get("created_at")) >= start]
            row = {
                "id": agent.get("id"),
                "full_name": agent.get("full_name"),
                "username": agent.get("username"),
                "email": agent.get("email"),
                "status": agent.get("status"),
                "created_at": agent.get("created_at"),
                "commission_payout": agent.get("commission_payout"),
                "flagged": agent.get("flagged", 0),
                "flagged_reason": agent.get("flagged_reason"),
                "flagged_at": agent.get("flagged_at"),
                "underpayment_attempts": agent.get("underpayment_attempts", 0),
                "total_quotes": len(qrows),
                "total_policies": len(prows),
                "total_premium": sum(float(q.get("total_payable") or 0) for q in qrows),
                "total_clients": len(clients),
            }
            rows.append(row)
        return rows

    def _policies_join(self, sql_l, params):
        filter_doc = {}
        if "where p.agent_id = %s" in sql_l or "where p.agent_id=%s" in sql_l:
            filter_doc["agent_id"] = int(params[0])
        elif "where policy_no=%s" in sql_l or "where p.policy_no=%s" in sql_l:
            filter_doc["policy_no"] = params[0]
        elif "where id=%s" in sql_l or "where p.id=%s" in sql_l:
            filter_doc["id"] = int(params[0])

        # Commission summary queries WHERE p.status='active'; only count active
        # policies, not the agent's whole book (incl. expired/cancelled).
        if "p.status='active'" in sql_l or "p.status = 'active'" in sql_l:
            filter_doc["status"] = "active"

        rows = self._find_many("policies", filter_doc, sort=[("created_at", DESCENDING)])

        if "date(p.created_at) >= %s" in sql_l and params:
            start = _as_date(params[0])
            if start is not None:
                rows = [r for r in rows if _as_date(r.get("created_at")) and _as_date(r.get("created_at")) >= start]

        if "vehicle_reg like %s" in sql_l and params:
            needle = str(params[0]).replace("%", "").upper()
            matched = []
            for row in rows:
                quote = self._find_one("quotations", {"id": row.get("quote_id")}) or {}
                vehicle_reg = str(row.get("vehicle_reg") or "").upper()
                chassis = str(quote.get("chassis_number") or "").upper()
                if needle in vehicle_reg or needle in chassis:
                    matched.append(row)
            rows = matched[:20]

        if "dmvic_status = 'issued'" in sql_l:
            rows = [r for r in rows if r.get("dmvic_status") == "issued" and not r.get("declared_at")]
            if params:
                company = params[0]
                rows = [r for r in rows if (self._find_one("quotations", {"id": r.get("quote_id")}) or {}).get("company") == company]

        if "datediff" in sql_l:
            rows = self._datediff_postfilter(rows, sql_l, params)

        enriched = []
        for row in rows:
            out = dict(row)
            client = self._find_one("clients", {"id": out.get("client_id")}) or {}
            quote = self._find_one("quotations", {"id": out.get("quote_id")}) or {}
            user = self._find_one("users", {"id": out.get("agent_id")}) or {}
            out.update({
                "first_name": client.get("first_name"),
                "last_name": client.get("last_name"),
                "phone": client.get("phone"),
                "client_email": client.get("email"),
                "client_name": " ".join(p for p in [client.get("first_name"), client.get("last_name")] if p),
                "agent_name": user.get("full_name"),
                "agent_email": user.get("email"),
                "vehicle_make": quote.get("make", ""),
            })
            for key in ("company", "policy_holder_name", "chassis_number", "make", "vehicle_body_type",
                        "year_of_manufacture", "engine_number", "kra_pin", "vehicle_value", "email"):
                out.setdefault(key, quote.get(key))
            enriched.append(out)
        return enriched

    def _declarations_history(self):
        rows = self._find_many("declarations", {}, sort=[("created_at", DESCENDING)], limit=200)
        for row in rows:
            user = self._find_one("users", {"id": row.get("created_by")}) or {}
            row["sent_by"] = user.get("full_name")
        return rows

    def _audit_log(self):
        rows = self._find_many("audit_log", {}, sort=[("created_at", DESCENDING)], limit=500)
        for row in rows:
            user = self._find_one("users", {"id": row.get("user_id")}) or {}
            row["full_name"] = user.get("full_name")
            row["username"] = user.get("username")
        return rows
