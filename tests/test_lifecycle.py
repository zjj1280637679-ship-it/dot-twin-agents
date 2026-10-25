"""Synthetic ports only: no native runtime, external agents, history or secrets."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from dot_twin import prepare
from dot_twin.lifecycle import Journal, Observation, Receipt, UnknownStart


SCOPE = "synthetic:session-1"


class SyntheticPort:
    port_id = SCOPE

    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior

    def spawn(self, request_id, request):
        self.calls.append((request_id, request.copy()))
        if self.behavior:
            return self.behavior(request_id, request)
        return Receipt(request_id, self.port_id, "synthetic-child:" + request_id)


def finding(request_id="r1", **changes):
    value = Observation(request_id, SCOPE, "synthetic-child:" + request_id,
                        "finding-1", "finding", "Synthetic finding: disputed premise.",
                        ("fixture:case-1",), ("Synthetic parent claim differs.",))
    return replace(value, **changes)


def final(request_id="r1", **changes):
    value = replace(finding(request_id), event_id="final-1", kind="final",
                    text="Synthetic final result.", outcome="completed")
    return replace(value, **changes)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "synthetic.sqlite"
        self.journal = Journal(self.path)
        self.request = prepare({"task_name": "check_delivery", "delta": "合成任务差异 🧭", "mode": "verify"})

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def create(self, request_id="r1"):
        return self.journal.create(self.request, port_id=SCOPE, request_id=request_id)

    def running(self, request_id="r1"):
        self.create(request_id)
        self.assertEqual(self.journal.dispatch(request_id, SyntheticPort()), "accepted")

    def test_helper_output_forwarded_exactly_without_mutation(self):
        self.create()
        before = json.dumps(self.request)
        def mutate(request_id, request):
            self.assertEqual(request, self.request)
            request["message"] = "adapter changed its private copy"
            return Receipt(request_id, SCOPE, "synthetic-child:r1")
        port = SyntheticPort(mutate)
        self.journal.dispatch("r1", port)
        self.assertEqual(json.dumps(self.request), before)
        self.assertEqual(self.journal.snapshot("r1")["request"], self.request)
        self.assertEqual(port.calls[0], ("r1", self.request))

    def test_full_lifecycle_preserves_evidence_disagreement_and_unverified_context(self):
        self.running()
        self.assertEqual(self.journal.collect(finding()), "accepted")
        self.assertEqual(self.journal.collect(final()), "accepted")
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["state"], "finished")
        self.assertEqual(snapshot["child_id"], "synthetic-child:r1")
        self.assertEqual(snapshot["context_inheritance"], "unverified")
        self.assertEqual(snapshot["observations"][0]["evidence"], ["fixture:case-1"])
        self.assertEqual(snapshot["observations"][1]["disagreements"], ["Synthetic parent claim differs."])
        self.assertFalse(snapshot["review_required"])
        self.assertEqual([event["kind"] for event in self.journal.history("r1")],
                         ["created", "dispatch", "receipt", "finding", "final"])

    def test_port_sees_committed_uncertainty_before_any_submission(self):
        self.create()
        def check(request_id, _):
            with Journal(self.path) as observer:
                self.assertEqual(observer.snapshot(request_id)["state"], "uncertain")
                self.assertEqual(observer.history(request_id)[-1]["kind"], "dispatch")
            return Receipt(request_id, SCOPE, "synthetic-child:r1")
        self.journal.dispatch("r1", SyntheticPort(check))

    def test_failed_intent_write_never_calls_port(self):
        self.create()
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER synthetic_intent_failure BEFORE INSERT ON audit
                WHEN NEW.kind='dispatch' BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        port = SyntheticPort()
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.dispatch("r1", port)
        self.assertEqual(port.calls, [])
        self.assertEqual(self.journal.snapshot("r1")["state"], "prepared")
        self.assertEqual(len(self.journal.history()), 1)

    def test_failed_pending_replay_rolls_back_binding_and_can_reconcile_again(self):
        self.create()
        def lost(*_):
            self.journal.collect(final())
            raise TimeoutError("synthetic missing receipt")
        port = SyntheticPort(lost)
        with self.assertRaises(UnknownStart):
            self.journal.dispatch("r1", port)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER synthetic_replay_failure BEFORE INSERT ON audit
                WHEN NEW.source_seq IS NOT NULL BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        receipt = Receipt("r1", SCOPE, "synthetic-child:r1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.reconcile("r1", receipt)
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["state"], "uncertain")
        self.assertIsNone(snapshot["child_id"])
        self.assertEqual(len(snapshot["pending"]), 1)
        self.assertFalse(any(event["kind"] == "receipt" for event in self.journal.history()))
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TRIGGER synthetic_replay_failure")
        self.assertEqual(self.journal.reconcile("r1", receipt), "accepted")
        self.assertEqual(self.journal.snapshot("r1")["state"], "finished")
        self.assertEqual(len(port.calls), 1)

    def test_lost_receipt_survives_reopen_and_cannot_resubmit(self):
        self.create()
        def lost(*_):
            raise TimeoutError("synthetic private adapter diagnostic")
        port = SyntheticPort(lost)
        with self.assertRaises(UnknownStart):
            self.journal.dispatch("r1", port)
        self.journal.close()
        self.journal = Journal(self.path)
        self.assertEqual(self.journal.snapshot("r1")["state"], "uncertain")
        with self.assertRaisesRegex(ValueError, "already_attempted"):
            self.journal.dispatch("r1", port)
        self.assertEqual(len(port.calls), 1)
        self.assertNotIn("private adapter diagnostic", json.dumps(self.journal.history()))
        self.journal.reconcile("r1", Receipt("r1", SCOPE, "synthetic-child:r1", evidence=("synthetic lookup",)))
        self.assertEqual(self.journal.collect(final()), "accepted")

    def test_abrupt_interruption_never_reopens_submission(self):
        self.create()
        def crash(*_):
            raise SystemExit("synthetic crash before receipt persistence")
        port = SyntheticPort(crash)
        with self.assertRaises(SystemExit):
            self.journal.dispatch("r1", port)
        self.assertEqual(self.journal.snapshot("r1")["state"], "uncertain")
        with self.assertRaisesRegex(ValueError, "already_attempted"):
            self.journal.dispatch("r1", port)
        self.assertEqual(len(port.calls), 1)

    def test_async_early_messages_rechecked_after_receipt_mainline_continues(self):
        self.create()
        entered, release = threading.Event(), threading.Event()
        def delayed(request_id, _):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("synthetic test deadline")
            return Receipt(request_id, SCOPE, "synthetic-child:r1")
        def submit():
            with Journal(self.path) as worker:
                return worker.dispatch("r1", SyntheticPort(delayed))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit)
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(future.done())
                self.assertEqual(self.journal.collect(final()), "pending_binding")
                self.assertEqual(self.journal.collect(finding()), "pending_binding")
                self.assertEqual(self.journal.collect(final()), "pending_binding")
                self.assertEqual(len(self.journal.snapshot("r1")["pending"]), 3)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5), "accepted")
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["state"], "finished")
        self.assertEqual([event["kind"] for event in snapshot["observations"]], ["final", "finding"])
        self.assertEqual(snapshot["pending"], [])
        resolved = [event for event in self.journal.history() if event["source_seq"] is not None]
        self.assertEqual([event["disposition"] for event in resolved], ["accepted", "accepted", "duplicate"])

    def test_wrong_child_pending_event_is_retained_and_quarantined_on_binding(self):
        self.create()
        def early(request_id, _):
            self.assertEqual(self.journal.collect(final(child_id="synthetic-intruder")), "pending_binding")
            return Receipt(request_id, SCOPE, "synthetic-child:r1")
        self.journal.dispatch("r1", SyntheticPort(early))
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["state"], "running")
        self.assertEqual(snapshot["observations"], [])
        self.assertEqual(snapshot["pending"], [])
        self.assertEqual(snapshot["issues"][0]["disposition"], "wrong_child")

    def test_wrong_request_receipt_cannot_bind_another_existing_request(self):
        self.create("r1")
        self.create("r2")
        port = SyntheticPort(lambda *_: Receipt("r2", SCOPE, "synthetic-child:r2"))
        self.assertEqual(self.journal.dispatch("r1", port), "wrong_request")
        self.assertEqual(self.journal.snapshot("r1")["state"], "uncertain")
        self.assertEqual(self.journal.snapshot("r2")["state"], "prepared")
        self.assertEqual(self.journal.history("r1")[-1]["payload"]["request_id"], "r2")

    def test_child_identity_cannot_be_bound_to_two_requests(self):
        self.running("r1")
        self.create("r2")
        port = SyntheticPort(lambda *_: Receipt("r2", SCOPE, "synthetic-child:r1"))
        self.assertEqual(self.journal.dispatch("r2", port), "child_already_bound")
        self.assertEqual(self.journal.snapshot("r2")["state"], "uncertain")
        self.assertEqual(self.journal.collect(final("r2", child_id="synthetic-child:r1")), "pending_binding")

    def test_same_task_name_and_out_of_order_children_use_real_identity(self):
        self.running("r1")
        self.running("r2")
        self.assertEqual(self.journal.collect(final("r2")), "accepted")
        self.assertEqual(self.journal.collect(final("r1", child_id="synthetic-child:r2")), "wrong_child")
        self.assertEqual(self.journal.snapshot("r1")["state"], "running")
        self.assertEqual(self.journal.collect(final("r1")), "accepted")
        self.assertEqual(self.journal.snapshot("r1")["observations"][0]["request_id"], "r1")

    def test_unknown_request_wrong_port_and_wrong_child_are_audited(self):
        self.running()
        self.assertEqual(self.journal.collect(final("unknown")), "unknown_request")
        self.assertEqual(self.journal.collect(final(port_id="synthetic:other-session")), "wrong_port")
        self.assertEqual(self.journal.collect(final(child_id="other-child")), "wrong_child")
        self.assertEqual(self.journal.reconcile("unknown", Receipt("unknown", SCOPE, "other")), "unknown_request")
        self.assertEqual(len(self.journal.history("unknown")), 2)
        self.assertEqual(self.journal.snapshot("r1")["state"], "running")
        self.assertEqual(self.journal.snapshot("r1")["observations"], [])

    def test_wrong_dispatch_port_is_never_called_and_does_not_claim(self):
        self.create()
        port = SyntheticPort()
        port.port_id = "synthetic:wrong"
        with self.assertRaisesRegex(ValueError, "wrong_port"):
            self.journal.dispatch("r1", port)
        self.assertEqual(port.calls, [])
        self.assertEqual(self.journal.snapshot("r1")["state"], "prepared")
        self.assertEqual(self.journal.dispatch("r1", SyntheticPort(lambda *_:
            Receipt("r1", "synthetic:wrong", "child"))), "wrong_port")
        self.assertEqual(self.journal.snapshot("r1")["state"], "uncertain")

    def test_exact_duplicate_and_renumbered_final_do_not_finish_twice(self):
        self.running()
        self.assertEqual(self.journal.collect(final()), "accepted")
        self.assertEqual(self.journal.collect(final()), "duplicate")
        self.assertEqual(self.journal.collect(final(event_id="redelivered-final")), "duplicate_final")
        self.assertEqual(len(self.journal.snapshot("r1")["observations"]), 1)
        self.assertFalse(self.journal.snapshot("r1")["review_required"])
        self.assertEqual(len([e for e in self.journal.history() if e["kind"] == "final"]), 3)

    def test_event_id_reuse_keeps_both_evidence_sets_and_flags_review(self):
        self.running()
        self.journal.collect(finding())
        changed = replace(finding(), evidence=("fixture:contrary-evidence",))
        self.assertEqual(self.journal.collect(changed), "event_conflict")
        snapshot = self.journal.snapshot("r1")
        self.assertTrue(snapshot["review_required"])
        self.assertEqual(snapshot["observations"][0]["evidence"], ["fixture:case-1"])
        self.assertEqual(snapshot["issues"][0]["payload"]["evidence"], ["fixture:contrary-evidence"])
        # Changing kind must not smuggle a final through an existing finding ID.
        self.assertEqual(self.journal.collect(final(event_id="finding-1")), "event_conflict")
        self.assertEqual(self.journal.snapshot("r1")["state"], "running")

    def test_conflicting_final_is_exposed_without_overwriting_first(self):
        self.running()
        self.journal.collect(final())
        contrary = replace(final(), event_id="final-2", text="Synthetic contrary conclusion.",
                           outcome="failed", disagreements=("Conflicts with first final.",))
        self.assertEqual(self.journal.collect(contrary), "final_conflict")
        snapshot = self.journal.snapshot("r1")
        self.assertTrue(snapshot["review_required"])
        self.assertEqual(snapshot["observations"][0]["outcome"], "completed")
        self.assertEqual(snapshot["issues"][0]["payload"]["outcome"], "failed")
        self.assertEqual(snapshot["issues"][0]["payload"]["disagreements"], ["Conflicts with first final."])

    def test_quarantined_and_renumbered_event_ids_cannot_be_reused_as_findings(self):
        self.running()
        self.journal.collect(final())
        contrary = replace(final(), event_id="final-2", text="Synthetic contrary conclusion.")
        self.assertEqual(self.journal.collect(contrary), "final_conflict")
        self.assertEqual(self.journal.collect(contrary), "duplicate")
        self.assertEqual(self.journal.collect(finding(event_id="final-2")), "event_conflict")
        self.assertEqual(self.journal.collect(final(event_id="renumbered")), "duplicate_final")
        self.assertEqual(self.journal.collect(final(event_id="renumbered")), "duplicate")
        self.assertEqual(self.journal.collect(finding(event_id="renumbered")), "event_conflict")
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(len(snapshot["observations"]), 1)
        self.assertTrue(snapshot["review_required"])

    def test_pending_messages_survive_reopen_and_are_replayed_only_once(self):
        self.create()
        def lost(*_):
            self.journal.collect(final())
            raise TimeoutError("synthetic missing receipt")
        with self.assertRaises(UnknownStart):
            self.journal.dispatch("r1", SyntheticPort(lost))
        self.journal.close()
        self.journal = Journal(self.path)
        self.assertEqual(len(self.journal.snapshot("r1")["pending"]), 1)
        receipt = Receipt("r1", SCOPE, "synthetic-child:r1")
        self.assertEqual(self.journal.reconcile("r1", receipt), "accepted")
        self.assertEqual(self.journal.reconcile("r1", receipt), "duplicate")
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["state"], "finished")
        self.assertEqual(snapshot["pending"], [])
        self.assertEqual(len(snapshot["observations"]), 1)
        self.assertEqual(len([e for e in self.journal.history() if e["source_seq"] is not None]), 1)

    def test_late_findings_and_failed_cancelled_finals_are_retained(self):
        for request_id, outcome in (("r1", "failed"), ("r2", "cancelled")):
            self.running(request_id)
            self.journal.collect(replace(final(request_id), outcome=outcome))
            self.assertEqual(self.journal.collect(finding(request_id)), "accepted")
            snapshot = self.journal.snapshot(request_id)
            self.assertEqual(snapshot["state"], "finished")
            self.assertEqual(snapshot["observations"][0]["outcome"], outcome)

    def test_explicit_nonadmission_requires_evidence_and_still_blocks_replay(self):
        self.create()
        report = Receipt("r1", SCOPE, None, "not_started", ("synthetic host admission rejection",))
        port = SyntheticPort(lambda *_: report)
        self.assertEqual(self.journal.dispatch("r1", port), "accepted")
        self.assertEqual(self.journal.snapshot("r1")["state"], "not_started")
        self.assertEqual(self.journal.reconcile("r1", report), "duplicate")
        with self.assertRaisesRegex(ValueError, "already_attempted"):
            self.journal.dispatch("r1", port)
        self.assertEqual(self.journal.reconcile("r1", Receipt("r1", SCOPE, "child")), "receipt_conflict")
        self.assertEqual(len(port.calls), 1)

    def test_rejection_resolves_pending_as_unexpected_without_erasing_it(self):
        self.create()
        def reject(request_id, _):
            self.journal.collect(final())
            return Receipt(request_id, SCOPE, None, "not_started", ("synthetic rejection",))
        self.journal.dispatch("r1", SyntheticPort(reject))
        snapshot = self.journal.snapshot("r1")
        self.assertEqual(snapshot["pending"], [])
        self.assertTrue(snapshot["review_required"])
        self.assertEqual(snapshot["issues"][0]["disposition"], "unexpected_state")

    def test_malformed_receipt_is_unknown_without_storing_its_contents(self):
        for number, report in enumerate(({}, Receipt("r1", SCOPE, None),
                                        Receipt("r1", SCOPE, None, "not_started"))):
            request_id = "r" + str(number + 1)
            self.create(request_id)
            with self.assertRaises(UnknownStart):
                self.journal.dispatch(request_id, SyntheticPort(lambda *_, report=report: report))
            self.assertEqual(self.journal.snapshot(request_id)["state"], "uncertain")
            self.assertEqual(self.journal.history(request_id)[-1]["disposition"], "invalid_receipt")

    def test_receipt_cannot_rebind_running_or_finished_child(self):
        self.running()
        self.journal.collect(final())
        self.assertEqual(self.journal.reconcile("r1", Receipt("r1", SCOPE, "different")), "receipt_conflict")
        self.assertEqual(self.journal.reconcile("r1", Receipt("r1", SCOPE, "synthetic-child:r1",
                         evidence=("synthetic additional host claim: inherited all",))), "duplicate")
        self.assertEqual(self.journal.snapshot("r1")["context_inheritance"], "unverified")
        self.assertEqual(self.journal.snapshot("r1")["child_id"], "synthetic-child:r1")

    def test_prepared_request_cannot_be_bound_by_an_unsolicited_result(self):
        self.create()
        self.assertEqual(self.journal.reconcile("r1", Receipt("r1", SCOPE, "child")), "unexpected_state")
        self.assertEqual(self.journal.collect(final()), "unexpected_state")
        self.assertEqual(self.journal.snapshot("r1")["state"], "prepared")

    def test_simultaneous_dispatches_make_only_one_port_call(self):
        self.create()
        barrier = threading.Barrier(2)
        port = SyntheticPort()
        def dispatch():
            with Journal(self.path) as worker:
                barrier.wait(timeout=5)
                try:
                    return worker.dispatch("r1", port)
                except ValueError as exc:
                    return str(exc)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(dispatch) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual(results, ["accepted", "already_attempted"])
        self.assertEqual(len(port.calls), 1)

    def test_simultaneous_delivery_is_accepted_only_once(self):
        self.running()
        barrier = threading.Barrier(2)
        def deliver():
            with Journal(self.path) as worker:
                barrier.wait(timeout=5)
                return worker.collect(final())
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(deliver) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual(results, ["accepted", "duplicate"])
        self.assertEqual(len(self.journal.snapshot("r1")["observations"]), 1)

    def test_reopen_and_snapshot_mutation_do_not_lose_or_change_records(self):
        self.running()
        self.journal.collect(finding())
        self.journal.collect(final())
        before = self.journal.snapshot("r1")
        self.journal.close()
        self.journal = Journal(self.path)
        self.assertEqual(self.journal.snapshot("r1"), before)
        before["observations"][0]["evidence"].append("caller mutation")
        before["request"]["message"] = "caller mutation"
        self.assertNotEqual(self.journal.snapshot("r1"), before)

    def test_invalid_envelopes_fail_before_writing_and_do_not_echo_task_text(self):
        self.running()
        before = self.journal.history()
        bad = (replace(finding(), event_id=""), replace(finding(), text="\ud800"),
               replace(finding(), evidence=["bad type"]), replace(finding(), disagreements=("\x00",)),
               replace(finding(), kind="bogus"), replace(finding(), outcome="completed"),
               replace(final(), outcome=None), {"history": "synthetic private task text"})
        for value in bad:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError) as caught:
                self.journal.collect(value)
            self.assertNotIn("private task text", str(caught.exception))
        self.assertEqual(self.journal.history(), before)

    def test_duplicate_request_id_and_invalid_prepared_shape_do_not_overwrite(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "request_exists"):
            self.create()
        for request in (dict(self.request, history=[]), dict(self.request, fork_turns="none"),
                        dict(self.request, task_name="../x")):
            with self.assertRaises(ValueError):
                self.journal.create(request, port_id=SCOPE)
        self.assertEqual(len(self.journal.history()), 1)

    def test_unrelated_database_and_future_schema_are_rejected(self):
        other = Path(self.tmp.name) / "unrelated.sqlite"
        with sqlite3.connect(other) as db:
            db.execute("CREATE TABLE existing(value TEXT)")
        before = hashlib.sha256(other.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "unsupported_journal"):
            Journal(other)
        self.assertEqual(hashlib.sha256(other.read_bytes()).hexdigest(), before)
        future = Path(self.tmp.name) / "future.sqlite"
        with sqlite3.connect(future) as db:
            db.execute(f"PRAGMA application_id={Journal._APPLICATION_ID}")
            db.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(ValueError, "unsupported_journal"):
            Journal(future)


if __name__ == "__main__":
    unittest.main()
