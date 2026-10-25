# Local lifecycle journal (schema 1)

`dot_twin.lifecycle` is an optional standard-library adapter boundary and SQLite
journal. `prepare()` and the `prepare` CLI keep their exact three-field output.
There is no bundled native adapter, polling loop, agent backend or context reader.
The Skill ZIP still contains only the Skill, manifest and MIT license; Python
integration requires this source package separately.

## Minimal integration

This runnable example is **synthetic**. It neither starts an agent nor demonstrates
native forking, permission inheritance or history copying.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from dot_twin import prepare
from dot_twin.lifecycle import Journal, Observation, Receipt

class SyntheticPort:
    port_id = "synthetic:demo-session-1"

    def spawn(self, request_id, request):
        # A real trusted adapter would call its native spawn exactly once with
        # **request, then normalize the actual returned identity. request_id is
        # local correlation metadata, not an additional native tool argument.
        return Receipt(request_id, self.port_id, "synthetic-child-1",
                       evidence=("synthetic fixture receipt",))

with TemporaryDirectory() as directory:
    with Journal(Path(directory) / "synthetic.sqlite") as journal:
        port = SyntheticPort()
        request_id = journal.create(
            prepare({"task_name": "verify_delivery", "delta": "Check the last mile.", "mode": "verify"}),
            port_id=port.port_id,
        )
        assert journal.dispatch(request_id, port) == "accepted"
        # The parent continues its own work. Its host callback later delivers:
        journal.collect(Observation(
            request_id, port.port_id, "synthetic-child-1", "host-event-1", "final",
            "Synthetic result; a premise remains disputed.",
            evidence=("fixture:case-1",), disagreements=("Parent premise differs.",),
            outcome="completed",
        ))
        view = journal.snapshot(request_id)
        assert view["state"] == "finished"
        assert view["context_inheritance"] == "unverified"
        assert not view["review_required"]
        # Inspect view["observations"] and view["issues"] together; assess evidence
        # before accepting conclusions or treating an external action as done.
```

The adapter must use the authoritative native child identity, not the requested
task name. `port_id` names a stable runtime/session namespace; do not reuse it for
another runtime that may recycle child IDs. Each host delivery needs an event ID
that remains stable across redelivery. When a host lacks event IDs, the adapter
must persist its own stable mapping; assigning a fresh ID per callback weakens
deduplication. No identity or message is authenticated by this local protocol.
Only trusted host callbacks should populate these envelopes. Evidence entries
and disagreements are explicit strings, retained exactly, never fetched or merged.

## States and recovery

| Transition | Meaning |
| --- | --- |
| `create` → `prepared` | Stable request ID, exact prepared request and port namespace saved; nothing submitted. |
| `dispatch` → `uncertain` | Submission intent committed **before** the sole port call. |
| Valid started receipt → `running` | Actual child ID bound uniquely within its port namespace. |
| Explicit nonadmission receipt → `not_started` | Host reports no child was admitted and supplies evidence. |
| First attributable final → `finished` | Completion reported; outcome is `completed`, `failed` or `cancelled`, not verified success. |

`dispatch` is one-shot for a request ID, including after restart or a confirmed
nonadmission. Exceptions raise `UnknownStart`; invalid receipts also raise it.
Correlation failures instead return a disposition such as `wrong_request` and
leave the request uncertain. A crash can leave `uncertain` even before a native
call occurred. Never infer nonadmission from a timeout, missing callback or an
eventually consistent "not found" lookup. Use the host's lookup/reconciliation
facility and call `journal.reconcile(originating_request_id, Receipt(...))` with
the actual report. Reconciliation cannot start another child. If the host cannot
resolve the outcome, retain uncertainty and report the blocker.

A `Receipt(..., child_id=None, outcome="not_started", evidence=(... ,))` must mean
explicit authoritative nonadmission, not an absent receipt. A retry, when justified,
requires an intentional new request ID outside this module. IDs are not unique
logical-task guarantees: creating two IDs for the same task can still start two
children. At-most-once applies only to this retained database and request ID;
database deletion, stale restores, a dishonest/retrying adapter or separate stores
are outside the guarantee. There is no distributed exactly-once claim.

## Collection and audit

`collect(Observation(...))` records a disposition for every valid envelope:

- `accepted`: attributable finding or first final. Late findings remain accepted.
- `pending_binding`: request is uncertain; retain the envelope until a valid receipt
  binds the child (or explicitly rejects admission). Replay is atomic with the
  receipt, in local arrival order, with `source_seq` linking each resolution to
  its original row. It survives restart and happens once.
- `duplicate`: identical event ID and envelope already seen for that bound child.
  `duplicate_final` also detects identical final content under another event ID.
- `event_conflict` / `final_conflict`: changed content under the same event ID,
  or another final with different content. Both versions remain available. Even
  quarantined conflict IDs cannot later be reused to sneak in a new finding.
- `unknown_request`, `wrong_request` (receipt), `wrong_port`, `wrong_child`,
  `child_already_bound` (receipt), `unexpected_state`, `receipt_conflict`: retained
  ownership/state problems; they do not bind a child or finish the wrong request.

Only well-formed `Receipt` / `Observation` values are journaled. Malformed direct
inputs raise `ValueError` without echoing their contents; a malformed dispatch
receipt records an empty `invalid_receipt` audit entry. Port exception text is
never stored. Evidence and disagreements use tuples of nonblank UTF-8 strings.

`snapshot()` returns fresh JSON values: exact request, state, actual child ID,
accepted observations, unresolved pending deliveries, issues and `review_required`.
Issues are historical and remain visible after reconciliation. A first accepted
final is not declared the correct final; competing payloads live in `issues`, and
must be reviewed with accepted observations. `history(request_id)` includes every
delivery and decision; `history()` also includes unknown-request quarantines.
Local `seq` and UTC `at` reflect receipt/replay order, not host execution time or
cross-child causality. No summarizer erases conflicting evidence.

## Local storage and host limits

Use an explicit private local file outside published artifacts. The journal stores
task differences, receipts and submitted result/evidence text as plaintext. It
does not discover files, read secrets, copy chats, fetch evidence or change native
permissions. The caller owns what it puts into task/result fields. Protect the
database and SQLite sidecars as task data. Do not commit them. The module rejects
unrelated databases and unsupported schema versions rather than migrating them.

Use one `Journal` connection per thread. SQLite `BEGIN IMMEDIATE` transactions
serialize competing claims/collections; the port call runs outside a transaction,
so the mainline can collect early events while submission is outstanding. Intent,
binding, replay and state changes commit atomically with their audit records.
Normal SQLite durability and filesystem assumptions apply. API audit rows are
append-only, not tamper-proof or cryptographically attested. Disk errors propagate;
do not delete/reset the file or retry a port call to work around them. Preserve the
store and reconcile if the outcome of a write or dispatch is uncertain.

The host still owns native availability, authorization, history copying, admission,
message delivery, resume/cancel, scheduling, isolation and billing. Lost host events
cannot be recovered without host support. `fork_turns="all"` is recorded intent;
**every snapshot says `context_inheritance="unverified"`**, even if receipt evidence
claims otherwise. Real context inheritance and external-action completion need
separate host evidence and review. This module does not certify either.

## Synthetic verification

All lifecycle tests use explicitly synthetic ports and temporary databases; no
external agent was started. The tests cover asynchronous early/late delivery,
out-of-order children, simultaneous submission/delivery, restart recovery,
duplicate/conflicting results, unknown requests, wrong ownership and uncertain
admission. They do not prove a native fork, copied context, actual host security,
live installation, crash/power-loss durability on every filesystem, cost savings
or correct real-world conclusions.

The checkout baseline is `b3a5d68a7e1c4fcf583d97dee6efa0675402dd88`.
Package version remains 0.1.0; no release artifact is published by this milestone.
