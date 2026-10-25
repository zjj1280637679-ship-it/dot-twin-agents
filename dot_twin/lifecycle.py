"""Trusted-local correlation journal; only a caller-supplied port can spawn.

This module never reads conversation history or credentials. A recorded receipt
is a host report, not proof of context inheritance or of a result's correctness.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Protocol
from uuid import uuid4

from .contracts import validate


@dataclass(frozen=True)
class Receipt:
    request_id: str
    port_id: str
    child_id: str | None
    outcome: str = "started"  # "not_started" requires explicit host evidence.
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Observation:
    request_id: str
    port_id: str
    child_id: str
    event_id: str
    kind: str  # finding | final
    text: str
    evidence: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()
    outcome: str | None = None  # completed | failed | cancelled for a final


class HostPort(Protocol):
    """Adapter owns native calls and normalizes authoritative host identities.

    port_id must identify a stable runtime/session namespace. request_id is
    adapter correlation metadata, never an extra native spawn argument.
    """
    port_id: str

    def spawn(self, request_id: str, request: dict) -> Receipt: ...


class UnknownStart(RuntimeError):
    """The port failed or returned an invalid receipt; reconcile, never retry."""


def _text(value):
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ValueError("invalid_text")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_text") from exc


def _evidence(value):
    if type(value) is not tuple:
        raise ValueError("invalid_evidence")
    for item in value:
        _text(item)


def _receipt(value):
    if type(value) is not Receipt:
        raise ValueError("invalid_receipt")
    for item in (value.request_id, value.port_id):
        _text(item)
    _evidence(value.evidence)
    if value.outcome == "started":
        _text(value.child_id)
    elif value.outcome != "not_started" or value.child_id is not None or not value.evidence:
        raise ValueError("invalid_receipt")
    return asdict(value)


def _observation(value):
    if type(value) is not Observation:
        raise ValueError("invalid_observation")
    for item in (value.request_id, value.port_id, value.child_id, value.event_id, value.text):
        _text(item)
    _evidence(value.evidence)
    _evidence(value.disagreements)
    if (value.kind not in ("finding", "final") or
        (value.kind == "finding" and value.outcome is not None) or
        (value.kind == "final" and value.outcome not in ("completed", "failed", "cancelled"))):
        raise ValueError("invalid_observation")
    return asdict(value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Journal:
    """One explicit SQLite file, one connection per thread; no background work.

    State is a cache; audit rows are append-only through this API. SQLite is not
    a tamper-proof store. Keep the file (including its SQLite sidecars) private.
    """
    _APPLICATION_ID = 0x4454574E  # DTWN

    def __init__(self, path):
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            with self._transaction():
                app = self._db.execute("PRAGMA application_id").fetchone()[0]
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                tables = self._db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if app == 0 and version == 0 and not tables:
                    self._db.execute("""CREATE TABLE requests (
                        request_id TEXT PRIMARY KEY, port_id TEXT NOT NULL,
                        request TEXT NOT NULL, state TEXT NOT NULL, child_id TEXT,
                        UNIQUE(port_id, child_id))""")
                    self._db.execute("""CREATE TABLE audit (
                        seq INTEGER PRIMARY KEY, at TEXT NOT NULL,
                        request_id TEXT NOT NULL, kind TEXT NOT NULL,
                        disposition TEXT NOT NULL, payload TEXT NOT NULL,
                        port_id TEXT, child_id TEXT, event_id TEXT, source_seq INTEGER UNIQUE)""")
                    self._db.execute("CREATE INDEX deliveries ON audit(port_id, child_id, event_id)")
                    self._db.execute(f"PRAGMA application_id={self._APPLICATION_ID}")
                    self._db.execute("PRAGMA user_version=1")
                elif app != self._APPLICATION_ID or version != 1:
                    raise ValueError("unsupported_journal")
        except BaseException:
            self._db.close()
            raise

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _request(self, request_id):
        return self._db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()

    def _append(self, request_id, kind, disposition, payload, source_seq=None):
        self._db.execute("""INSERT INTO audit
            (at,request_id,kind,disposition,payload,port_id,child_id,event_id,source_seq)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), request_id, kind, disposition, _json(payload),
             payload.get("port_id"), payload.get("child_id"), payload.get("event_id"), source_seq))
        return disposition

    def create(self, request, *, port_id, request_id=None):
        """Register the unchanged output of prepare(); no native call is made."""
        if type(request) is not dict or set(request) != {"task_name", "message", "fork_turns"}:
            raise ValueError("invalid_prepared_request")
        validate({"task_name": request["task_name"], "delta": request["message"]})
        if request["fork_turns"] != "all":
            raise ValueError("invalid_prepared_request")
        request_id = str(uuid4()) if request_id is None else request_id
        _text(request_id)
        _text(port_id)
        payload = _json(request)
        with self._transaction():
            if self._request(request_id) is not None:
                raise ValueError("request_exists")
            self._db.execute("INSERT INTO requests VALUES (?,?,?,'prepared',NULL)",
                             (request_id, port_id, payload))
            self._append(request_id, "created", "accepted", {"port_id": port_id, "request": request})
        return request_id

    def dispatch(self, request_id, port: HostPort):
        """Commit uncertainty BEFORE invoking the supplied port, at most once.

        Exceptions, crashes and malformed receipts never reset to prepared.
        Even a confirmed not_started request cannot be dispatched a second time.
        """
        _text(request_id)
        with self._transaction():
            row = self._request(request_id)
            if row is None:
                raise ValueError("unknown_request")
            if row["port_id"] != port.port_id:
                raise ValueError("wrong_port")
            if row["state"] != "prepared":
                raise ValueError("already_attempted")
            self._db.execute("UPDATE requests SET state='uncertain' WHERE request_id=?", (request_id,))
            self._append(request_id, "dispatch", "accepted", {"port_id": row["port_id"]})
            request = json.loads(row["request"])
        try:
            receipt = port.spawn(request_id, request)
        except Exception:
            # Exception text may contain task data or adapter secrets.
            with self._transaction():
                self._append(request_id, "dispatch_error", "unknown_start", {})
            raise UnknownStart(request_id) from None
        try:
            _receipt(receipt)
        except ValueError:
            with self._transaction():
                self._append(request_id, "receipt", "invalid_receipt", {})
            raise UnknownStart(request_id) from None
        return self.reconcile(request_id, receipt)

    def reconcile(self, request_id, receipt: Receipt):
        """Attach an actual host receipt to its originating request, never spawn."""
        _text(request_id)
        payload = _receipt(receipt)
        with self._transaction():
            row = self._request(request_id)
            if row is None:
                disposition = "unknown_request"
            elif receipt.request_id != request_id:
                disposition = "wrong_request"
            elif receipt.port_id != row["port_id"]:
                disposition = "wrong_port"
            elif row["state"] == "prepared":
                disposition = "unexpected_state"
            elif row["state"] != "uncertain":
                same = (receipt.child_id == row["child_id"] and
                        (receipt.outcome == "not_started") == (row["state"] == "not_started"))
                disposition = "duplicate" if same else "receipt_conflict"
            elif receipt.outcome == "not_started":
                self._db.execute("UPDATE requests SET state='not_started' WHERE request_id=?", (request_id,))
                disposition = "accepted"
            elif self._db.execute("SELECT 1 FROM requests WHERE port_id=? AND child_id=?",
                                  (receipt.port_id, receipt.child_id)).fetchone():
                disposition = "child_already_bound"
            else:
                self._db.execute("UPDATE requests SET state='running', child_id=? WHERE request_id=?",
                                 (receipt.child_id, request_id))
                disposition = "accepted"
            self._append(request_id, "receipt", disposition, payload)
            if disposition == "accepted":
                pending = self._db.execute("""SELECT * FROM audit AS a WHERE request_id=?
                    AND disposition='pending_binding' AND NOT EXISTS
                    (SELECT 1 FROM audit AS b WHERE b.source_seq=a.seq) ORDER BY seq""",
                    (request_id,)).fetchall()
                for event in pending:
                    self._collect(json.loads(event["payload"]), event["seq"])
            return disposition

    def collect(self, observation: Observation):
        """Keep each valid delivery, including duplicates and rejected ownership.

        Messages arriving before a receipt are retained and rechecked on binding.
        No findings, evidence, disagreements or conflicting finals are merged.
        """
        payload = _observation(observation)
        # Normalize tuples to their JSON representation for exact comparisons.
        payload = json.loads(_json(payload))
        with self._transaction():
            return self._collect(payload)

    def _collect(self, payload, source_seq=None):
        request_id = payload["request_id"]
        row = self._request(request_id)
        if row is None:
            disposition = "unknown_request"
        elif payload["port_id"] != row["port_id"]:
            disposition = "wrong_port"
        elif row["state"] in ("prepared", "not_started"):
            disposition = "unexpected_state"
        elif row["child_id"] is None:
            disposition = "pending_binding"
        elif payload["child_id"] != row["child_id"]:
            disposition = "wrong_child"
        else:
            previous = self._db.execute("""SELECT payload FROM audit WHERE port_id=?
                AND child_id=? AND event_id=? AND disposition IN
                ('accepted','event_conflict','final_conflict','duplicate_final')
                AND kind IN ('finding','final')""",
                (payload["port_id"], payload["child_id"], payload["event_id"])).fetchall()
            final = self._db.execute("""SELECT payload FROM audit WHERE request_id=?
                AND kind='final' AND disposition='accepted'""", (request_id,)).fetchone()
            if previous:
                disposition = ("duplicate" if any(json.loads(event[0]) == payload for event in previous)
                               else "event_conflict")
            elif payload["kind"] == "final" and final is not None:
                prior = json.loads(final[0])
                disposition = ("duplicate_final" if
                    {k: v for k, v in prior.items() if k != "event_id"} ==
                    {k: v for k, v in payload.items() if k != "event_id"} else "final_conflict")
            else:
                disposition = "accepted"
                if payload["kind"] == "final":
                    self._db.execute("UPDATE requests SET state='finished' WHERE request_id=?", (request_id,))
        return self._append(request_id, payload["kind"], disposition, payload, source_seq)

    def history(self, request_id=None):
        """Return fresh JSON values in local receipt order, including quarantines."""
        query, args = "SELECT * FROM audit", ()
        if request_id is not None:
            query, args = query + " WHERE request_id=?", (request_id,)
        rows = self._db.execute(query + " ORDER BY seq", args).fetchall()
        return [dict(row, payload=json.loads(row["payload"])) for row in rows]

    def snapshot(self, request_id):
        """Completion is observed, not verified; inspect issues before using it."""
        with self._transaction():
            row = self._request(request_id)
            if row is None:
                raise ValueError("unknown_request")
            history = self.history(request_id)
            resolved = {event["source_seq"] for event in history if event["source_seq"] is not None}
            issues = [event for event in history if event["disposition"] not in
                      ("accepted", "duplicate", "duplicate_final", "pending_binding")]
            return {"request_id": request_id, "port_id": row["port_id"],
                    "request": json.loads(row["request"]), "state": row["state"],
                    "child_id": row["child_id"], "context_inheritance": "unverified",
                    "observations": [event["payload"] for event in history if
                                     event["kind"] in ("finding", "final") and event["disposition"] == "accepted"],
                    "pending": [event for event in history if event["disposition"] == "pending_binding"
                                and event["seq"] not in resolved],
                    "issues": issues, "review_required": bool(issues)}
