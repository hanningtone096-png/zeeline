"""In-memory stand-in for the pymongo API surface used by MongoStore.

Only the operations MongoStore actually calls are implemented: equality /
$in / $or / $exists / $gte / $lt filters, $set / $inc / $setOnInsert updates,
find / find_one / find_one_and_update, count_documents, insert / update /
delete, and cursors with sort+limit. Runs with the standard library only —
no MongoDB server and no extra pip installs.

Run the suite that uses this file with:
    python -m unittest discover -s backend/tests -v
"""
from datetime import datetime

ASCENDING = 1
DESCENDING = -1


class ReturnDocument:
    BEFORE = False
    AFTER = True


class _InsertResult:
    def __init__(self):
        self.inserted_id = None


class _WriteResult:
    def __init__(self, count):
        self.modified_count = count
        self.deleted_count = count


def _type_rank(value):
    """Mongo's canonical BSON sort order (subset): null < numbers < strings < dates."""
    if value is None:
        return (0, 0)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, (int, float)):
        return (1, value)
    if isinstance(value, str):
        return (2, value)
    if isinstance(value, datetime):
        return (3, value)
    return (4, str(value))


def _match_value(condition, value):
    if isinstance(condition, dict):
        for op, arg in condition.items():
            if op == "$in":
                if value not in arg:
                    return False
            elif op == "$exists":
                # Our store never keeps truly missing fields — absent ≈ None.
                if bool(value is not None) != bool(arg):
                    return False
            elif op == "$ne":
                if value == arg:
                    return False
            elif op == "$gte":
                if value is None or not _comparable(value, arg) or value < arg:
                    return False
            elif op == "$lt":
                if value is None or not _comparable(value, arg) or value >= arg:
                    return False
            else:
                raise NotImplementedError(f"Unsupported query operator: {op}")
        return True
    return value == condition


def _comparable(a, b):
    try:
        a < b
        return True
    except TypeError:
        return False


def _match(doc, flt):
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
        elif not _match_value(cond, doc.get(key)):
            return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    return {k: v for k, v in doc.items() if projection.get(k)}


def _apply_update(doc, update):
    for op, fields in update.items():
        if op == "$set":
            doc.update(fields)
        elif op == "$inc":
            for k, n in fields.items():
                doc[k] = (doc.get(k) or 0) + n
        elif op == "$setOnInsert":
            for k, v in fields.items():
                doc.setdefault(k, v)
        else:
            raise NotImplementedError(f"Unsupported update operator: {op}")


class FakeCursor:
    def __init__(self, docs, projection=None):
        self._docs = docs
        self._projection = projection
        self._limit = 0

    def sort(self, spec):
        # pymongo-style stable multi-key sort: apply keys weakest-first.
        for key, direction in reversed(list(spec)):
            self._docs.sort(key=lambda d: _type_rank(d.get(key)),
                            reverse=(direction < 0))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __iter__(self):
        docs = self._docs[: self._limit] if self._limit else self._docs
        for d in docs:
            yield _project(dict(d), self._projection)


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self._docs = []

    def create_index(self, *args, **kwargs):
        return self.name

    def insert_one(self, doc):
        self._docs.append(dict(doc))
        return _InsertResult()

    def insert_many(self, docs):
        self._docs.extend(dict(d) for d in docs)
        return _InsertResult()

    def find(self, flt=None, projection=None):
        return FakeCursor([d for d in self._docs if _match(d, flt)],
                          projection)

    def find_one(self, flt=None, projection=None):
        for d in self._docs:
            if _match(d, flt):
                return _project(dict(d), projection)
        return None

    def update_many(self, flt, update):
        n = 0
        for d in self._docs:
            if _match(d, flt):
                _apply_update(d, update)
                n += 1
        return _WriteResult(n)

    def update_one(self, flt, update, upsert=False):
        for d in self._docs:
            if _match(d, flt):
                _apply_update(d, update)
                return _WriteResult(1)
        if upsert:
            new_doc = {}
            _apply_update(new_doc, update)
            self._docs.append(new_doc)
            return _WriteResult(1)
        return _WriteResult(0)

    def delete_many(self, flt):
        keep = [d for d in self._docs if not _match(d, flt)]
        removed = len(self._docs) - len(keep)
        self._docs[:] = keep
        return _WriteResult(removed)

    def count_documents(self, flt):
        return sum(1 for d in self._docs if _match(d, flt))

    def find_one_and_update(self, flt, update, upsert=False,
                            return_document=ReturnDocument.AFTER):
        for d in self._docs:
            if _match(d, flt):
                before = dict(d)
                _apply_update(d, update)
                return dict(d) if return_document else before
        if upsert:
            new_doc = {}
            _apply_update(new_doc, update)
            self._docs.append(new_doc)
            return dict(new_doc) if return_document else None
        return None


class FakeDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name)
        return self._collections[name]

    def __getattr__(self, name):
        return self[name]