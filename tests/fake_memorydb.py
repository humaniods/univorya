"""A small in-process HTTP fake of the MemoryDB v0.2 document API.

It exists so the integration tests can exercise the real HTTP adapter without a
Linux/Docker host.  Only the routes and semantics Univorya relies on are
implemented: collections, document create/read/patch/delete, queries with
``where``/``or``/``not``/``order_by``/``offset``, optimistic versions, unique
fields, idempotency keys and record-level expiry.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from functools import cmp_to_key
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

TOKEN = "test-admin-token-0123456789"

_MISSING = object()
_TYPES = {
    "any": lambda value: True,
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fold(value) -> str:
    """ASCII-only case folding, matching MemoryDB's ``iequals``/``icontains``."""
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in str(value)
    )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _field(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _rank(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return 1
    if isinstance(value, str):
        return 2
    if isinstance(value, list):
        return 3
    if isinstance(value, dict):
        return 4
    return 5


def _compare_values(left, right) -> int:
    left_key, right_key = (_rank(left), left), (_rank(right), right)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def _matches(predicate: dict, data: dict) -> bool:
    op = predicate.get("op")
    expected = predicate.get("value")
    value = _field(data, predicate.get("field", ""))

    if op == "exists":
        present = value is not _MISSING and value is not None
        return present is bool(expected)
    if value is _MISSING:
        return False
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op in ("gt", "gte", "lt", "lte"):
        try:
            if op == "gt":
                return value > expected
            if op == "gte":
                return value >= expected
            if op == "lt":
                return value < expected
            return value <= expected
        except TypeError:
            return False
    if op == "in":
        return isinstance(expected, list) and value in expected
    if op == "contains":
        if isinstance(value, list):
            return expected in value
        return str(expected) in str(value)
    if op == "icontains":
        if isinstance(value, list):
            return any(_fold(item) == _fold(expected) for item in value)
        return _fold(expected) in _fold(value)
    if op == "prefix":
        return str(value).startswith(str(expected))
    if op == "iequals":
        return _fold(value) == _fold(expected)
    return False


class FakeMemoryDB:
    """A thread-per-connection MemoryDB stand-in bound to 127.0.0.1."""

    def __init__(self, token: str = TOKEN):
        self.token = token
        self.collections: dict[str, dict] = {}
        self.records: dict[str, dict[str, dict]] = {}
        self.idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self.sequence = 0
        self.lock = threading.RLock()
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle
    @property
    def url(self) -> str:
        assert self.httpd is not None, "server is not running"
        return f"memorydb://{self.token}@127.0.0.1:{self.httpd.server_address[1]}"

    def start(self) -> "FakeMemoryDB":
        fake = self

        class Handler(_Handler):
            server_ref = fake

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    # -------------------------------------------------------------- helpers
    def _active(self, collection: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        result = []
        for record in self.records.get(collection, {}).values():
            if record.get("deleted"):
                continue
            expiry = record.get("expires")
            if expiry:
                try:
                    if _parse_time(expiry) <= now:
                        continue
                except ValueError:
                    continue
            result.append(record)
        return result

    def _bump(self, record: dict) -> None:
        record["version"] = int(record.get("version", 0)) + 1
        record["updated"] = _now()
        self.sequence += 1

    def _duplicate_field(self, collection: str, data: dict, exclude_id=None):
        schema = (self.collections.get(collection) or {}).get("schema") or {}
        fields = schema.get("fields") or {}
        for field in schema.get("unique") or []:
            value = data.get(field)
            if value is None:
                continue
            fold = bool((fields.get(field) or {}).get("casefold"))
            for record in self._active(collection):
                if record["id"] == exclude_id:
                    continue
                other = record["data"].get(field)
                if other is None:
                    continue
                same = _fold(value) == _fold(other) if fold else value == other
                if same:
                    return field
        return None

    def _validate(self, collection: str, data: dict) -> str | None:
        schema = (self.collections.get(collection) or {}).get("schema") or {}
        fields = schema.get("fields") or {}
        for name, definition in fields.items():
            if definition.get("required") and data.get(name) is None:
                return f"missing required field {name!r}"
        for name, value in data.items():
            definition = fields.get(name)
            if not definition or value is None:
                continue
            check = _TYPES.get(definition.get("type") or "any")
            if check and not check(value):
                return f"field {name!r} must be {definition.get('type')}"
        return None

    def _filtered(self, collection: str, body: dict) -> list[dict]:
        rows = self._active(collection)
        conditions = body.get("where") or []
        or_groups = body.get("or") or []
        negations = body.get("not") or []
        matched = []
        for record in rows:
            data = record["data"]
            if not all(_matches(predicate, data) for predicate in conditions):
                continue
            if or_groups and not any(
                all(_matches(predicate, data) for predicate in group) for group in or_groups
            ):
                continue
            if any(_matches(predicate, data) for predicate in negations):
                continue
            matched.append(record)
        return matched

    def _sort(self, rows: list[dict], body: dict) -> list[dict]:
        order_by = body.get("order_by")
        if not order_by:
            return rows
        if isinstance(order_by, str):
            terms = [(order_by, bool(body.get("descending")))]
        else:
            terms = [(term["field"], bool(term.get("descending"))) for term in order_by]

        def compare(left, right):
            for field, descending in terms:
                left_value = _field(left["data"], field)
                right_value = _field(right["data"], field)
                if left_value is _MISSING and right_value is _MISSING:
                    continue
                # Missing values sort after present ones in either direction.
                if left_value is _MISSING:
                    return 1
                if right_value is _MISSING:
                    return -1
                result = _compare_values(left_value, right_value)
                if result:
                    return -result if descending else result
            return -1 if left["id"] < right["id"] else (1 if left["id"] > right["id"] else 0)

        return sorted(rows, key=cmp_to_key(compare))

    def _project(self, record: dict, projection) -> dict:
        if not projection:
            return record
        data = {}
        for path in projection:
            value = _field(record["data"], path)
            if value is not _MISSING:
                data[path] = value
        return {**record, "data": data, "projection": projection}


class _Handler(BaseHTTPRequestHandler):
    server_ref: FakeMemoryDB

    # ----------------------------------------------------------- HTTP basics
    def log_message(self, *args):  # keep pytest output clean
        pass

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _route(self, method: str):
        fake = self.server_ref
        path = urlsplit(self.path).path
        if not path.startswith("/api/v1/"):
            return self._error(404, "not found")
        if self.headers.get("Authorization") != f"Bearer {fake.token}":
            return self._error(401, "invalid token")
        path = path[len("/api/v1/"):]

        with fake.lock:
            if path == "health/" and method == "GET":
                return self._send(200, {
                    "status": "ok", "version": "0.2.0-fake",
                    "identity": "admin", "sequence": fake.sequence,
                })
            if path == "collections/":
                if method == "GET":
                    results = [
                        {"collection": name, "name": name,
                         "documents": len(fake._active(name)),
                         "schema": definition.get("schema") or {}}
                        for name, definition in fake.collections.items()
                    ]
                    return self._send(200, {"results": results, "total": len(results)})
                if method == "POST":
                    body = self._body()
                    if not body or not body.get("name"):
                        return self._error(400, "name is required")
                    name = body["name"]
                    if name in fake.collections:
                        return self._error(409, "collection already exists")
                    fake.collections[name] = {"schema": body.get("schema") or {}}
                    fake.records.setdefault(name, {})
                    return self._send(201, {"name": name, "schema": fake.collections[name]["schema"]})
                return self._error(405, "method not allowed")

            if path.startswith("documents/"):
                parts = [unquote(part) for part in path.strip("/").split("/")]
                if len(parts) == 2 and method in ("GET", "POST"):
                    collection = parts[1]
                    if collection not in fake.collections:
                        return self._error(404, "unknown collection")
                    if method == "GET":
                        rows = fake._active(collection)[:50]
                        return self._send(200, {"results": rows, "total": len(rows)})
                    return self._create(collection)
                if len(parts) == 3:
                    collection, record_id = parts[1], parts[2]
                    if collection not in fake.collections:
                        return self._error(404, "unknown collection")
                    if method == "GET":
                        return self._read(collection, record_id)
                    if method in ("PUT", "PATCH"):
                        return self._patch(collection, record_id, replace=method == "PUT")
                    if method == "DELETE":
                        return self._delete(collection, record_id)
                return self._error(404, "not found")

            if path.startswith("query/") and method == "POST":
                collection = unquote(path.strip("/").split("/")[1])
                if collection not in fake.collections:
                    return self._error(404, "unknown collection")
                body = self._body() or {}
                rows = fake._filtered(collection, body)
                total = len(rows)
                rows = fake._sort(rows, body)
                try:
                    limit = max(1, min(int(body.get("limit") or 50), 500))
                    offset = max(0, int(body.get("offset") or 0))
                except (TypeError, ValueError):
                    return self._error(400, "limit/offset must be integers")
                page = rows[offset:offset + limit]
                return self._send(200, {
                    "results": [fake._project(record, body.get("projection")) for record in page],
                    "total": total, "sequence": fake.sequence,
                })

            return self._error(404, "not found")

    # ---------------------------------------------------------- record CRUD
    def _create(self, collection: str):
        fake = self.server_ref
        body = self._body()
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            return self._error(400, "data object is required")
        data = body["data"]
        idem = self.headers.get("Idempotency-Key")
        if idem:
            fingerprint = hashlib.sha256(
                json.dumps(body, sort_keys=True).encode("utf-8")
            ).hexdigest()
            previous = fake.idempotency.get((self.path, idem))
            if previous:
                if previous[0] != fingerprint:
                    return self._error(409, "idempotency key reused with different contents")
                record = fake.records[collection].get(previous[1])
                if record:
                    return self._send(201, record)
        problem = fake._validate(collection, data)
        if problem:
            return self._error(400, problem)
        duplicate = fake._duplicate_field(collection, data)
        if duplicate:
            return self._error(409, f"unique field {duplicate!r} already exists")
        expires = body.get("expires")
        if expires:
            try:
                _parse_time(expires)
            except ValueError:
                return self._error(400, "expires must be RFC3339")
        record_id = uuid.uuid4().hex
        record = {
            "id": record_id,
            "collection": collection,
            "version": 1,
            "data": data,
            "visibility": body.get("visibility") or "private",
            "created": _now(),
            "updated": _now(),
        }
        if expires:
            record["expires"] = expires
        fake.records[collection][record_id] = record
        fake.sequence += 1
        if idem:
            fake.idempotency[(self.path, idem)] = (
                hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest(),
                record_id,
            )
        return self._send(201, record)

    def _live(self, collection: str, record_id: str):
        fake = self.server_ref
        record = fake.records.get(collection, {}).get(record_id)
        if not record or record.get("deleted"):
            return None
        expiry = record.get("expires")
        if expiry:
            try:
                if _parse_time(expiry) <= datetime.now(timezone.utc):
                    return None
            except ValueError:
                return None
        return record

    def _read(self, collection: str, record_id: str):
        record = self._live(collection, record_id)
        if not record:
            return self._error(404, "record not found")
        return self._send(200, record)

    def _patch(self, collection: str, record_id: str, replace: bool = False):
        fake = self.server_ref
        record = self._live(collection, record_id)
        if not record:
            return self._error(404, "record not found")
        body = self._body()
        if not isinstance(body, dict):
            return self._error(400, "JSON object is required")
        current_version = int(record.get("version", 0))
        supplied_version = body.get("version")
        has_set = "set" in body or "unset" in body
        if has_set and supplied_version is not None and int(supplied_version) != current_version:
            return self._error(409, "version conflict")
        if has_set and supplied_version is None and not replace:
            return self._error(409, "version is required for set")

        if replace:
            if not isinstance(body.get("data"), dict):
                return self._error(400, "data object is required")
            prospective = body["data"]
            problem = fake._validate(collection, prospective)
            if problem:
                return self._error(400, problem)
            duplicate = fake._duplicate_field(collection, prospective, exclude_id=record_id)
            if duplicate:
                return self._error(409, f"unique field {duplicate!r} already exists")
            record["data"] = prospective
            fake._bump(record)
            return self._send(200, record)

        set_fields = body.get("set") or {}
        unset_fields = body.get("unset") or []
        increments = body.get("inc") or {}
        if not isinstance(set_fields, dict) or not isinstance(unset_fields, list) \
                or not isinstance(increments, dict):
            return self._error(400, "invalid patch body")

        prospective = {**record["data"], **set_fields}
        for field in unset_fields:
            prospective.pop(field, None)
        problem = fake._validate(collection, prospective)
        if problem:
            return self._error(400, problem)
        duplicate = fake._duplicate_field(collection, prospective, exclude_id=record_id)
        if duplicate:
            return self._error(409, f"unique field {duplicate!r} already exists")

        record["data"].update(set_fields)
        for field in unset_fields:
            record["data"].pop(field, None)
        for field, step in increments.items():
            if not isinstance(step, (int, float)) or isinstance(step, bool):
                return self._error(400, "inc steps must be numbers")
            current = record["data"].get(field, 0)
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                return self._error(400, f"field {field!r} is not numeric")
            record["data"][field] = current + step
        fake._bump(record)
        return self._send(200, record)

    def _delete(self, collection: str, record_id: str):
        fake = self.server_ref
        record = self._live(collection, record_id)
        if not record:
            return self._error(404, "record not found")
        if_match = self.headers.get("If-Match")
        if if_match is None or if_match != str(record.get("version")):
            return self._error(409, "If-Match version required")
        record["deleted"] = True
        record["deleted_at"] = _now()
        fake._bump(record)
        return self._send(200, {"deleted": True, "id": record_id,
                                "version": record["version"]})

    # --------------------------------------------------------------- verbs
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")
