import json
import os
import time
import unittest
from unittest import mock

from juno import config
from juno.adapters import fs
from juno.adapters import status as io
from juno.core import status
from juno.core.ledger import Segment
from tests.support import SandboxCase

SID = "2b1f0c7e-1d2a-4c3b-9e8f-0a1b2c3d4e5f"
OLD_SID = "aaaaaaaa-1d2a-4c3b-9e8f-0a1b2c3d4e5f"
NOW = 1_793_000_000.0


def build(sid: str | None = SID, previous: dict | None = None, **over: object) -> dict:
    args: dict = {
        "sid": sid,
        "now": NOW,
        "previous": previous or {},
        "repo": "app",
        "ticket": None,
        "card_dirty": False,
        "card_auto": True,
        "card_path": None,
        "packs_injected": 1,
        "packs_cap": 4,
        "rules_added_segment": 0,
        "errors_24h": 0,
        "last_error": None,
    }
    args.update(over)
    return status.build_status(**args)


class Build(unittest.TestCase):
    def test_fields_are_the_v1_set_without_grant_fields(self) -> None:
        got = build()
        self.assertEqual(
            sorted(got),
            sorted(
                [
                    "v",
                    "updated",
                    "segment_id",
                    "interactive",
                    "repo",
                    "ticket",
                    "card_dirty",
                    "card_auto",
                    "card_path",
                    "packs_injected",
                    "packs_cap",
                    "rules_added_segment",
                    "rules_added_seq",
                    "last_rule",
                    "last_reminder_seq",
                    "errors_24h",
                    "last_error",
                ]
            ),
        )
        self.assertEqual(got["v"], 1)
        self.assertRegex(got["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")

    def test_counters_carry_over_and_increase(self) -> None:
        first = build(rule={"scope": "global", "text": "a"})
        second = build(previous=first, reminded=True)
        self.assertEqual((second["rules_added_seq"], second["last_reminder_seq"]), (1, 1))
        self.assertEqual(second["last_rule"], {"scope": "global", "text": "a"})
        third = build(previous=second)
        self.assertEqual((third["rules_added_seq"], third["last_reminder_seq"]), (1, 1))

    def test_pure_function_does_not_touch_its_inputs(self) -> None:
        previous = {"rules_added_seq": 2, "last_rule": {"scope": "x", "text": "y"}}
        copy = json.loads(json.dumps(previous))
        build(previous=previous)
        self.assertEqual(previous, copy)


class Files(SandboxCase):
    def own(self, sid: str) -> dict:
        with open(os.path.join(self.sb.state, "status", sid + ".json"), encoding="utf-8") as h:
            return json.load(h)

    def shared(self) -> dict:
        with open(os.path.join(self.sb.state, "status.json"), encoding="utf-8") as h:
            return json.load(h)

    def test_session_file_matches_shared_file(self) -> None:
        got = build()
        io.write_status(self.sb.state, got)
        self.assertEqual((self.own(SID), self.shared()), (got, got))
        self.assertEqual(os.stat(os.path.join(self.sb.state, "status")).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(self.sb.state, "status.json")).st_mode & 0o777, 0o600)

    def test_two_sessions_keep_their_own_state(self) -> None:
        io.write_status(self.sb.state, build(SID, ticket="ABC-1"))
        io.write_status(self.sb.state, build(OLD_SID, ticket="ABC-2"))
        self.assertEqual(self.own(SID)["ticket"], "ABC-1")
        self.assertEqual(self.shared()["segment_id"], OLD_SID)

    def test_read_status_prefers_own_then_matching_shared(self) -> None:
        got = build()
        io.write_status(self.sb.state, got)
        self.assertEqual(io.read_status(self.sb.state), got)
        self.assertEqual(io.read_status(self.sb.state, SID), got)
        self.assertEqual(io.read_status(self.sb.state, OLD_SID), {})
        os.unlink(os.path.join(self.sb.state, "status", SID + ".json"))
        self.assertEqual(io.read_status(self.sb.state, SID), got)

    def test_unsafe_session_id_writes_only_shared(self) -> None:
        for sid in ("../../etc/x", None, ""):
            io.write_status(self.sb.state, build(sid))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "status")))
        self.assertIsNone(status.session_status_path(self.sb.state, "a/b"))
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "status.json")))

    def test_crash_keeps_both_files(self) -> None:
        io.write_status(self.sb.state, build())
        before = (self.own(SID), self.shared())
        with mock.patch.object(fs.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                io.write_status(self.sb.state, build(reminded=True))
        self.assertEqual((self.own(SID), self.shared()), before)
        names = os.listdir(os.path.join(self.sb.state, "status"))
        self.assertEqual([n for n in names if n.startswith(".juno-tmp-")], [])

    def test_prune_removes_old_session_files(self) -> None:
        io.write_status(self.sb.state, build(SID))
        io.write_status(self.sb.state, build(OLD_SID))
        old = time.time() - 2 * 86400
        os.utime(os.path.join(self.sb.state, "status", OLD_SID + ".json"), (old, old))
        io.prune_session_files(self.sb.state, time.time())
        self.assertEqual(os.listdir(os.path.join(self.sb.state, "status")), [SID + ".json"])
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "status.json")))

    def test_garbage_status_reads_as_empty(self) -> None:
        os.makedirs(self.sb.state)
        with open(os.path.join(self.sb.state, "status.json"), "w", encoding="utf-8") as h:
            h.write("{broken")
        self.assertEqual(io.read_status(self.sb.state), {})


class Update(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = config.load(self.sb.env)
        self.seg = Segment(SID)

    def test_update_builds_through_the_core_and_writes_both_files(self) -> None:
        got = io.update(self.cfg, self.seg)
        self.assertEqual(sorted(got), sorted(build()))
        self.assertEqual(io.read_status(self.sb.state, SID), got)
        self.assertEqual(io.read_status(self.sb.state), got)

    def test_counters_survive_between_updates(self) -> None:
        io.update(self.cfg, self.seg, rule={"scope": "global", "text": "a"})
        again = io.update(self.cfg, self.seg, reminded=True)
        self.assertEqual((again["rules_added_seq"], again["last_reminder_seq"]), (1, 1))
        self.assertEqual(again["last_rule"], {"scope": "global", "text": "a"})

    def test_error_log_feeds_the_summary(self) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        os.makedirs(self.sb.state)
        with open(os.path.join(self.sb.state, "hook-errors.log"), "w", encoding="utf-8") as handle:
            handle.write(f"{stamp} stop ValueError x.py:3 'boom'\n")
        got = io.update(self.cfg, self.seg)
        self.assertEqual((got["errors_24h"], got["last_error"]["kind"]), (1, "ValueError"))
        self.assertEqual(io.repeated_errors(self.cfg), [])


if __name__ == "__main__":
    unittest.main()
