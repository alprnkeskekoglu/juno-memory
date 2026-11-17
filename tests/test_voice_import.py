import io
import json
import os
import time
from typing import Any
from unittest import mock

from juno import cli
from juno.adapters import voiceio
from tests.support.hooks import SID
from tests.test_voice import VoiceCase

DAY = 86400.0


def word(i: int) -> str:
    """A letters-only token, unique per i (no digits: ids are a privacy finding)."""
    out = ""
    for _ in range(3):
        out += chr(97 + i % 26)
        i //= 26
    return out


def msg(i: int) -> str:
    return f"ok merged it {word(i)} fine"


def jsonl(items: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(item) + "\n" for item in items)


class ImportCase(VoiceCase):
    def put(self, text: str, name: str = "in.txt") -> str:
        path = os.path.join(self.sb.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def voice(
        self, *argv: str, env: dict[str, str] | None = None, stdin: str = ""
    ) -> tuple[int, str]:
        out = io.StringIO()
        data = io.TextIOWrapper(io.BytesIO(stdin.encode()))
        with mock.patch("sys.stdout", out), mock.patch("sys.stdin", data):
            code = cli.main(["voice", *argv], {**self.env, **(env or {})})
        return code, out.getvalue()

    def imp(self, text: str, *flags: str) -> dict[str, Any]:
        code, out = self.voice("import", "--json", *flags, self.put(text))
        self.assertEqual(code, 0, out)
        report: dict[str, Any] = json.loads(out)
        return report

    def voice_md(self) -> str:
        path = os.path.join(self.sb.vault, "self", "voice.md")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def state_bytes(self) -> dict[str, bytes | None]:
        paths = {
            "rows": os.path.join(self.sb.state, "voice.jsonl"),
            "names": os.path.join(self.sb.state, "voice-names.json"),
            "md": os.path.join(self.sb.vault, "self", "voice.md"),
        }
        found: dict[str, bytes | None] = {}
        for key, path in paths.items():
            if os.path.exists(path):
                with open(path, "rb") as handle:
                    found[key] = handle.read()
            else:
                found[key] = None
        return found

    def imported(self) -> list[dict[str, Any]]:
        return [r for r in self.rows() if r.get("by") == "import"]


class Core(ImportCase):
    def test_1_twenty_messages_become_import_rows(self) -> None:
        items = [{"text": msg(i)} for i in range(20)]
        report = self.imp(jsonl(items), "--register", "voice")
        rows = self.imported()
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(r["k"] == "sample" and r["reg"] == "voice" for r in rows))
        self.assertTrue(all(r["sid"] == f"import:{report['batch']}" for r in rows))
        self.assertEqual((report["accepted"], report["skipped"]), (20, 0))
        self.assertEqual(report["registers"]["voice"]["accepted"], 20)
        mode = os.stat(os.path.join(self.sb.state, "voice.jsonl")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_2_second_run_adds_nothing_and_reports_duplicates(self) -> None:
        text = jsonl([{"text": msg(i)} for i in range(20)])
        self.imp(text)
        again = self.imp(text)
        self.assertEqual(len(self.imported()), 20)
        self.assertEqual(again["accepted"], 0)
        self.assertEqual(again["registers"]["voice"]["skipped"]["duplicate"], 20)

    def test_3_plain_text_paragraphs(self) -> None:
        text = f"{msg(1)}\nsecond line\n\n\n{msg(2)}\n\n  \n{msg(3)}\n"
        report = self.imp(text)
        self.assertEqual(report["accepted"], 3)
        self.assertEqual(self.imported()[0]["text"], f"{msg(1)}\nsecond line")

    def test_stdin_dash_reads_the_input(self) -> None:
        code, out = self.voice("import", "--json", "-", stdin=msg(1) + "\n\n" + msg(2))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["accepted"], 2)

    def test_4_register_defaults_from_target(self) -> None:
        items = [
            {"text": msg(1), "target": "tracker"},
            {"text": msg(2), "target": "chat"},
            {"text": msg(3)},
            {"text": msg(4), "target": "mail"},
            {"text": msg(5), "target": "review"},
            {"text": msg(6), "target": "code"},
            {"text": msg(7), "target": "tracker", "register": "voice"},
        ]
        self.imp(jsonl(items))
        regs = {r["text"]: r["reg"] for r in self.imported()}
        self.assertEqual(regs[msg(1)], "formal")
        self.assertEqual(regs[msg(2)], "voice")
        self.assertEqual(regs[msg(3)], "voice")
        self.assertEqual(regs[msg(4)], "formal")
        self.assertEqual(regs[msg(5)], "voice")
        self.assertEqual(regs[msg(6)], "formal")
        self.assertEqual(regs[msg(7)], "voice")

    def test_flags_fill_rows_without_values_and_rows_win(self) -> None:
        text = jsonl([{"text": msg(1)}, {"text": msg(2), "register": "voice"}])
        self.imp(text, "--register", "formal", "--target", "mail")
        rows = {r["text"]: r for r in self.imported()}
        self.assertEqual((rows[msg(1)]["reg"], rows[msg(1)]["kind"]), ("formal", "mail"))
        self.assertEqual(rows[msg(2)]["reg"], "voice")

    def test_5_each_skip_reason_is_counted_once(self) -> None:
        quoted = "> wrote this\n> and that\nok"
        items = [
            {"text": msg(1)},
            {"text": "mail me at someone@example.com please"},
            {"text": "a"},
            {"text": quoted},
            {"text": "---------- Forwarded message ---------\nFrom: x\nok then"},
            {"text": "Begin forwarded message:\nok then"},
        ]
        report = self.imp(jsonl(items))
        skipped = report["registers"]["voice"]["skipped"]
        self.assertEqual(skipped["privacy"], 1)
        self.assertEqual(skipped["short"], 1)
        self.assertEqual(skipped["quoted"], 3)
        self.assertEqual(report["accepted"], 1)
        self.assertNotIn("someone@example.com", json.dumps(self.rows()))

    def test_token_like_secret_is_skipped_not_masked(self) -> None:
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        report = self.imp(jsonl([{"text": f"here is the token {secret} ok"}]))
        self.assertEqual(report["registers"]["voice"]["skipped"]["privacy"], 1)
        self.assertEqual(self.rows(), [])

    def test_6_known_name_is_counted_but_never_an_example(self) -> None:
        items = [{"text": msg(i)} for i in range(14)]
        items.append({"text": "ok ask Cornelius about it"})
        report = self.imp(jsonl(items), "--register", "voice")
        self.assertEqual(report["registers"]["voice"]["counted"], 15)
        self.assertTrue(report["registers"]["voice"]["ready"])
        body = self.voice_md()
        self.assertIn("## voice (15 samples)", body)
        self.assertNotIn("cornelius", body.casefold())
        self.assertIn("ok merged it", body)
        with open(os.path.join(self.sb.state, "voice-names.json"), encoding="utf-8") as handle:
            self.assertIn("cornelius", json.load(handle))

    def test_7_limit_keeps_the_newest_and_old_messages_are_skipped(self) -> None:
        now = time.time()
        items = [{"text": msg(i), "t": now - 60.0 * i} for i in range(500)]
        items.append({"text": msg(900), "t": now - 400 * DAY})
        report = self.imp(jsonl(items))
        part = report["registers"]["voice"]
        self.assertEqual(part["accepted"], 300)
        self.assertEqual(part["skipped"]["over_limit"], 200)
        self.assertEqual(part["skipped"]["too_old"], 1)
        texts = {r["text"] for r in self.imported()}
        self.assertIn(msg(0), texts)
        self.assertIn(msg(299), texts)
        self.assertNotIn(msg(300), texts)

    def test_import_time_uses_the_given_send_time_with_z(self) -> None:
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10 * DAY))
        self.imp(jsonl([{"text": msg(1), "t": iso}, {"text": msg(2)}]))
        rows = {r["text"]: r for r in self.imported()}
        self.assertAlmostEqual(rows[msg(1)]["t"], time.time() - 10 * DAY, delta=5)
        self.assertAlmostEqual(rows[msg(2)]["t"], time.time(), delta=5)

    def test_max_age_and_max_follow_the_config(self) -> None:
        self.set_config("[voice]\nimport_max = 2\nimport_max_age_days = 10\n")
        now = time.time()
        items = [{"text": msg(i), "t": now - DAY * i} for i in range(5)]
        items.append({"text": msg(9), "t": now - 20 * DAY})
        part = self.imp(jsonl(items))["registers"]["voice"]
        self.assertEqual((part["accepted"], part["skipped"]["over_limit"]), (2, 3))
        self.assertEqual(part["skipped"]["too_old"], 1)

    def test_8_live_samples_survive_a_full_import(self) -> None:
        self.seed([f"live {word(i)} text" for i in range(400)], by="user")
        self.imp(jsonl([{"text": msg(i)} for i in range(300)]))
        rows = self.rows()
        self.assertEqual(len([r for r in rows if r["by"] == "user"]), 400)
        self.assertEqual(len([r for r in rows if r["by"] == "import"]), 300)

    def test_pruned_caps_imports_apart_from_live_rows(self) -> None:
        rows = [
            {"k": "sample", "id": f"l{i}", "t": float(i), "by": "user", "text": "x"}
            for i in range(voiceio.MAX_SAMPLES + 5)
        ] + [
            {"k": "sample", "id": f"i{i}", "t": float(i), "by": "import", "text": "x"}
            for i in range(20)
        ]
        kept = voiceio.pruned(rows, 7)
        self.assertEqual(len([r for r in kept if r["by"] == "user"]), voiceio.MAX_SAMPLES)
        self.assertEqual(
            {r["id"] for r in kept if r["by"] == "import"}, {f"i{i}" for i in range(13, 20)}
        )

    def test_9_profile_is_written_at_the_threshold_without_a_session_end(self) -> None:
        self.assertEqual(self.voice_md(), "")
        report = self.imp(jsonl([{"text": msg(i)} for i in range(15)]))
        self.assertTrue(report["profile"])
        body = self.voice_md()
        self.assertIn("## voice (15 samples)", body)
        self.assertIn("length: median", body)
        self.assertIn("Examples (verbatim", body)

    def test_below_the_threshold_writes_no_profile(self) -> None:
        report = self.imp(jsonl([{"text": msg(i)} for i in range(5)]))
        self.assertFalse(report["profile"])
        self.assertEqual(self.voice_md(), "")

    def test_10_dry_run_changes_no_file_and_reports_the_same(self) -> None:
        self.seed(["live one here", "live two here"], by="user")
        before = self.state_bytes()
        text = jsonl([{"text": msg(i)} for i in range(16)] + [{"text": "ask Cornelius ok"}])
        path = self.put(text)
        code, out = self.voice("import", "--json", "--dry-run", path)
        self.assertEqual(code, 0)
        self.assertEqual(self.state_bytes(), before)
        dry = json.loads(out)
        real = self.imp(text)
        self.assertTrue(dry["dry_run"] and not real["dry_run"])
        dry.pop("dry_run"), real.pop("dry_run")
        self.maxDiff = None
        self.assertEqual(dry, real)
        self.assertTrue(real["profile"])

    def test_text_report_names_batch_and_counts(self) -> None:
        code, out = self.voice("import", self.put(msg(1) + "\n\na"))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"batch [0-9a-f]{8}")
        self.assertIn("voice: 1 accepted, 1 skipped (short 1), 1 counted, not ready", out)

    def test_11_forget_removes_the_batch_and_rebuilds_or_removes_the_profile(self) -> None:
        self.seed([f"live {word(i)} text" for i in range(10)], by="user")
        keep = self.imp(jsonl([{"text": msg(i)} for i in range(10)]))
        self.assertIn("## voice (20 samples)", self.voice_md())
        drop = self.imp(jsonl([{"text": msg(i)} for i in range(100, 105)]))
        self.assertIn("## voice (25 samples)", self.voice_md())
        code, out = self.voice("forget", "--batch", drop["batch"], "--json")
        self.assertEqual((code, json.loads(out)["removed"]), (0, 5))
        self.assertEqual({r["sid"] for r in self.imported()}, {f"import:{keep['batch']}"})
        self.assertIn("## voice (20 samples)", self.voice_md())
        code, _ = self.voice("forget", "--batch", keep["batch"])
        self.assertEqual(code, 0)
        self.assertEqual(self.imported(), [])
        self.assertEqual(len(self.rows()), 10)
        self.assertEqual(self.voice_md(), "")

    def test_forget_unknown_batch_changes_nothing(self) -> None:
        self.imp(jsonl([{"text": msg(1)}]))
        before = self.state_bytes()
        code, out = self.voice("forget", "--batch", "deadbeef", "--json")
        self.assertEqual((code, json.loads(out)["removed"]), (0, 0))
        self.assertEqual(self.state_bytes(), before)

    def test_session_end_keeps_imported_rows_and_counts_them(self) -> None:
        self.imp(jsonl([{"text": msg(i)} for i in range(14)]))
        self.prompt("post this message: fine with me")
        self.post("fine with me")
        self.hook("session_end", self.payload("session_end", reason="other"))
        self.assertEqual(len(self.imported()), 14)
        self.assertIn("## voice (15 samples)", self.voice_md())


class Refusals(ImportCase):
    def test_12_nosave_segment_refuses_and_changes_nothing(self) -> None:
        self.prompt("hello [nosave]")
        before = self.state_bytes()
        env = {"CLAUDE_CODE_SESSION_ID": SID}
        code, out = self.voice("import", self.put(msg(1)), env=env)
        self.assertEqual(code, 2)
        self.assertIn("refused", out)
        self.assertIn("[nosave]", out)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.voice("forget", "--batch", "abcd1234", env=env)[0], 2)

    def test_private_segment_refuses(self) -> None:
        self.prompt("hello there")
        with open(
            os.path.join(self.sb.state, "ledgers", SID + ".jsonl"), "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps({"ev": "private", "t": 5.0}) + "\n")
        code, _ = self.voice("import", self.put(msg(1)), env={"CLAUDE_CODE_SESSION_ID": SID})
        self.assertEqual(code, 2)
        self.assertEqual(self.rows(), [])

    def test_subagent_refuses(self) -> None:
        code, out = self.voice("import", self.put(msg(1)), env={"CLAUDE_CODE_AGENT_ID": "a1"})
        self.assertEqual(code, 2)
        self.assertIn("agent context", out)
        self.assertEqual(self.rows(), [])

    def test_normal_session_and_plain_shell_are_allowed(self) -> None:
        self.prompt("hello there")
        code, _ = self.voice("import", self.put(msg(1)), env={"CLAUDE_CODE_SESSION_ID": SID})
        self.assertEqual(code, 0)
        self.assertEqual(self.voice("import", self.put(msg(2)))[0], 0)
        self.assertEqual(len(self.imported()), 2)

    def test_disabled_voice_refuses(self) -> None:
        self.set_config("[voice]\nenabled = false\n")
        self.assertEqual(self.voice("import", self.put(msg(1)))[0], 2)

    def test_missing_file_is_a_refusal_not_a_crash(self) -> None:
        code, out = self.voice("import", os.path.join(self.sb.root, "nope.txt"))
        self.assertEqual(code, 2)
        self.assertIn("cannot read", out)


class Privacy(ImportCase):
    def test_13_marker_is_only_in_state_and_voice_md(self) -> None:
        marker = "zanzibarquux"
        self.prompt("post this message: fine with me")
        old = time.time() - DAY
        items = [{"text": msg(i), "t": old} for i in range(14)] + [{"text": f"ok {marker} done"}]
        self.imp(jsonl(items))
        self.assertIn(marker, self.voice_md())
        self.hook("session_end", self.payload("session_end", reason="other"))
        for base in (self.sb.vault, os.path.join(self.sb.state, "ledgers")):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if name == "voice.md":
                        continue
                    with open(os.path.join(root, name), encoding="utf-8", errors="replace") as h:
                        self.assertNotIn(marker, h.read(), os.path.join(root, name))
        self.assertTrue(os.listdir(os.path.join(self.sb.state, "ledgers")))

    def test_import_writes_no_ledger_record(self) -> None:
        self.prompt("hello there")
        before = self.ledger()
        self.imp(jsonl([{"text": msg(1)}]))
        self.assertEqual(self.ledger(), before)


class Status(ImportCase):
    def test_14_status_splits_the_counted_samples(self) -> None:
        self.seed(["a b c"] * 2, by="user")
        self.seed(["d e f"], by="edited")
        self.seed(["g h i"], by="claude")
        self.seed(["j k l"], reg="formal", by="user")
        self.imp(jsonl([{"text": msg(i)} for i in range(4)]))
        code, out = self.voice("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["min_samples"], 15)
        self.assertEqual(
            data["registers"]["voice"],
            {"user": 2, "edited": 1, "import": 4, "counted": 7, "min_samples": 15, "ready": False},
        )
        self.assertEqual(data["registers"]["formal"]["user"], 1)
        code, text = self.voice("status")
        self.assertIn("voice: 7 counted (user 2, edited 1, import 4) of 15, not ready", text)

    def test_status_reads_a_ready_register(self) -> None:
        self.imp(jsonl([{"text": msg(i)} for i in range(15)]))
        data = json.loads(self.voice("status", "--json")[1])
        self.assertTrue(data["registers"]["voice"]["ready"])


class Doctor(ImportCase):
    def doctor(self) -> str:
        return self.sb.cli("doctor", *self.sb.install_args())[1]

    def turns(self, n: int, sid: str = SID) -> None:
        os.makedirs(os.path.join(self.sb.state, "ledgers"), exist_ok=True)
        path = os.path.join(self.sb.state, "ledgers", sid + ".jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            for i in range(n):
                rec = {"ev": "prompt", "pid": f"p{i}", "part": 0, "text": "hi", "t": 1.0 + i}
                handle.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def test_suggests_import_after_enough_turns_without_a_profile(self) -> None:
        self.turns(30, "a1")
        self.assertNotIn("voice import", self.doctor())
        self.turns(20, "b2")
        self.assertIn("INFO voice:", self.doctor())
        self.assertIn("juno voice import", self.doctor())

    def test_silent_when_ready_or_disabled(self) -> None:
        self.turns(60)
        self.imp(jsonl([{"text": msg(i)} for i in range(15)]))
        self.assertNotIn("voice import", self.doctor())
        self.assertEqual(self.voice("forget", "--batch", self.last_batch())[0], 0)
        self.assertIn("voice import", self.doctor())
        self.set_config("[voice]\nenabled = false\n")
        self.assertNotIn("voice import", self.doctor())

    def last_batch(self) -> str:
        return str(self.imported()[0]["sid"]).split(":", 1)[1]
