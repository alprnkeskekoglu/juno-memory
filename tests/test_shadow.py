import json
import os
import stat
import unittest
from typing import Any

from juno import config
from juno.adapters import index, prune, shadowio
from juno.core import shadow
from tests.support import SandboxCase
from tests.support.hooks import HookCase

DAY = 86400


def record(
    state: str, segment: str, prompt: str, suggestions: Any = (), now: int = 1000, **kw: Any
) -> None:
    shadowio.append(state, shadow.build_entry(now, segment, prompt, suggestions, **kw))


class Entries(SandboxCase):
    def lines(self) -> list[dict[str, Any]]:
        with open(os.path.join(self.sb.state, "shadow.jsonl"), encoding="utf-8") as handle:
            return [json.loads(x) for x in handle]

    def test_secrets_redacted(self) -> None:
        secrets = [
            "ghp_" + "a" * 36,
            "AKIA" + "A" * 16,
            "xoxb-123456789012-abcdefghij",
            "sk_live_" + "b" * 20,
            "npm_" + "d" * 36,
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefghijkl",
            "Bearer abcdefghijklmnopqrstuvwx",
            "sk-" + "g" * 24,
            "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----",
            "api_key=supersecretvalue123",
        ]
        for secret in secrets:
            record(self.sb.state, "seg", f"before {secret} after")
        for line, secret in zip(self.lines(), secrets, strict=True):
            self.assertIn("[redacted", line["prompt"], secret)
            self.assertNotIn(secret[:14], line["prompt"], secret)
            self.assertTrue(line["prompt"].startswith("before "))

    def test_short_value_not_redacted(self) -> None:
        record(self.sb.state, "seg", "token=abc and key: 123")
        self.assertEqual(self.lines()[0]["prompt"], "token=abc and key: 123")

    def test_cut_at_200_after_filter(self) -> None:
        record(self.sb.state, "seg", "x" * 190 + "ghp_" + "z" * 36)
        record(self.sb.state, "seg", "y" * 500)
        a, b = self.lines()
        self.assertNotIn("ghp_", a["prompt"])
        self.assertLessEqual(len(a["prompt"]), 200)
        self.assertEqual(len(b["prompt"]), 200)

    def test_file_mode_and_fields(self) -> None:
        sugg = [{"relpath": "notes/a.md", "heading": "H", "score": 1.5, "snippet": "SECRET BODY"}]
        record(self.sb.state, "seg1", "hello", sugg)
        mode = os.stat(os.path.join(self.sb.state, "shadow.jsonl")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)
        entry = self.lines()[0]
        self.assertEqual(
            entry["suggestions"], [{"path": "notes/a.md", "heading": "H", "score": 1.5}]
        )
        self.assertEqual(entry["segment"], "seg1")
        self.assertNotIn("SECRET BODY", json.dumps(entry))

    def test_arms_only_when_given(self) -> None:
        record(self.sb.state, "a", "one", arms={"demo": "on"})
        record(self.sb.state, "b", "two", arms={})
        record(self.sb.state, "c", "three")
        self.assertEqual([e.get("arms", "-") for e in self.lines()], [{"demo": "on"}, "-", "-"])

    def test_suggestion_without_path_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            shadow.build_entry(1, "s", "p", [{"score": 1.0}])

    def test_caps_on_lists(self) -> None:
        many = [{"path": f"n/{i}.md", "score": 1.0} for i in range(30)]
        entry = shadow.build_entry(1, "s", "p", many, delivered=[str(i) for i in range(40)])
        self.assertEqual((len(entry["suggestions"]), len(entry["delivered"])), (10, 20))

    def test_suggestable_skips_injected_files(self) -> None:
        self.assertFalse(shadow.suggestable("core.md"))
        self.assertFalse(shadow.suggestable("rules/global.md"))
        self.assertFalse(shadow.suggestable("protocol/rules.md"))
        self.assertTrue(shadow.suggestable("knowledge/k.md"))


class Retention(SandboxCase):
    def lines(self) -> list[dict[str, Any]]:
        return shadowio.read(self.sb.state)[0]

    def test_retention_prune(self) -> None:
        now = 1_000_000_000
        record(self.sb.state, "s", "old", now=now - 31 * DAY)
        record(self.sb.state, "s", "edge", now=now - 29 * DAY)
        record(self.sb.state, "s", "new", now=now)
        self.assertEqual(shadowio.prune(self.sb.state, 30, now), (2, 1))
        self.assertEqual([e["prompt"] for e in self.lines()], ["edge", "new"])
        mode = os.stat(os.path.join(self.sb.state, "shadow.jsonl")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    def test_prune_without_file(self) -> None:
        self.assertEqual(shadowio.prune(self.sb.state, 30), (0, 0))

    def test_bad_lines_are_dropped_by_prune_and_counted_by_read(self) -> None:
        record(self.sb.state, "s", "ok", now=2_000_000_000)
        with open(shadowio.shadow_path(self.sb.state), "a", encoding="utf-8") as handle:
            handle.write("{not json\n[1]\n")
        self.assertEqual(shadowio.read(self.sb.state)[1], 2)
        self.assertEqual(shadowio.prune(self.sb.state, 365, 2_000_000_000), (1, 2))
        self.assertEqual(shadowio.read(self.sb.state)[1], 0)

    def test_session_start_pruning_uses_the_configured_days(self) -> None:
        cfg = config.load(self.sb.env)
        record(self.sb.state, "s", "ancient", now=1)
        record(self.sb.state, "s", "recent", now=int(__import__("time").time()))
        prune.prune_state(cfg)
        self.assertEqual([e["prompt"] for e in self.lines()], ["recent"])
        self.assertEqual(cfg.get_int("retention", "shadow_days"), 365)


class Merge(SandboxCase):
    def write_v1(self, name: str, entries: list[dict[str, Any]]) -> str:
        path = os.path.join(self.sb.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def v1(self, ts: int, segment: str, prompt: str) -> dict[str, Any]:
        return {"ts": ts, "segment": segment, "prompt": prompt, "suggestions": [], "arm": "cards"}

    def test_archive_and_live_logs_overlap_once(self) -> None:
        archive = self.write_v1("a.jsonl", [self.v1(10, "s1", "one"), self.v1(20, "s1", "two")])
        live = self.write_v1("b.jsonl", [self.v1(20, "s1", "two"), self.v1(30, "s2", "three")])
        first = shadowio.merge_log(self.sb.state, archive)
        second = shadowio.merge_log(self.sb.state, live)
        self.assertEqual((first["added"], first["skipped"]), (2, 0))
        self.assertEqual((second["added"], second["skipped"], second["total"]), (1, 1, 3))
        self.assertEqual([e["ts"] for e in shadowio.read(self.sb.state)[0]], [10, 20, 30])

    def test_same_second_different_prompt_or_segment_are_distinct(self) -> None:
        path = self.write_v1(
            "c.jsonl", [self.v1(5, "s1", "a"), self.v1(5, "s1", "b"), self.v1(5, "s2", "a")]
        )
        self.assertEqual(shadowio.merge_log(self.sb.state, path)["added"], 3)

    def test_rerunning_the_merge_is_a_no_op(self) -> None:
        path = self.write_v1("d.jsonl", [self.v1(1, "s", "p")])
        shadowio.merge_log(self.sb.state, path)
        before = shadowio.shadow_path(self.sb.state)
        stamp = os.stat(before).st_mtime_ns
        again = shadowio.merge_log(self.sb.state, path)
        self.assertEqual((again["added"], again["skipped"]), (0, 1))
        self.assertEqual(os.stat(before).st_mtime_ns, stamp)

    def test_invalid_lines_are_counted_and_missing_source_is_empty(self) -> None:
        path = self.write_v1("e.jsonl", [self.v1(1, "s", "p")])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("garbage\n")
        self.assertEqual(shadowio.merge_log(self.sb.state, path)["invalid"], 1)
        missing = shadowio.merge_log(self.sb.state, os.path.join(self.sb.root, "none.jsonl"))
        self.assertEqual((missing["added"], missing["total"]), (0, 1))

    def test_merged_entries_keep_their_fields(self) -> None:
        path = self.write_v1("f.jsonl", [self.v1(1, "s", "p")])
        shadowio.merge_log(self.sb.state, path)
        self.assertEqual(shadowio.read(self.sb.state)[0][0]["arm"], "cards")


class HookLogging(HookCase):
    TICKETS = False

    def setUp(self) -> None:
        super().setUp()
        self.set_config("[shadow]\nbudget_ms = 5000\n")
        self.write("knowledge/zebra.md", "# Zebra\n\nzebra stripes migration notes\n")
        self.write("rules/global.md", "# Rules\n\nzebra stripes migration rule\n")
        self.cfg = config.load(self.env)

    def shadow_config(self, **values: str) -> None:
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        extra = "".join(f"{k} = {v}\n" for k, v in values.items())
        for key in values:
            text = "".join(
                line + "\n" for line in text.splitlines() if not line.startswith(key + " =")
            )
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text.replace("[shadow]\n", "[shadow]\n" + extra))

    def prompt(self, text: str, **env: str) -> dict[str, Any] | None:
        payload = self.payload("UserPromptSubmit", prompt=text)
        return self.hook("user_prompt_submit", payload, env=env or None)

    def entries(self) -> list[dict[str, Any]]:
        return shadowio.read(self.sb.state)[0]

    def test_logs_prompt_with_suggestions_once_the_index_exists(self) -> None:
        index.sync(self.cfg)
        self.prompt("tell me about zebra stripes migration")
        entries = self.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["segment"], "57fb7685-9be8-4bc7-a435-47aa50ccd35f")
        paths = [s["path"] for s in entries[0]["suggestions"]]
        self.assertEqual(paths, ["knowledge/zebra.md"])
        self.assertNotIn("arms", entries[0])
        self.assertEqual(self.errors(), "")

    def test_no_index_no_entry(self) -> None:
        self.prompt("tell me about zebra stripes migration")
        self.assertEqual(self.entries(), [])

    def test_disabled_nosave_and_subagent_log_nothing(self) -> None:
        index.sync(self.cfg)
        self.prompt("zebra stripes [nosave]")
        self.hook(
            "user_prompt_submit",
            self.payload("UserPromptSubmit", prompt="zebra stripes", agent_id="a1"),
        )
        self.shadow_config(enabled="false")
        self.prompt("zebra stripes migration again")
        self.assertEqual(self.entries(), [])

    def test_headless_run_logs_nothing(self) -> None:
        index.sync(self.cfg)
        self.hook(
            "user_prompt_submit",
            self.payload("UserPromptSubmit", prompt="zebra stripes"),
            env={"JUNO_TEST_INTERACTIVE": "0", "CLAUDE_CODE_SESSION_ATTENDED": "0"},
        )
        self.assertEqual(self.entries(), [])

    def test_secrets_never_reach_the_log(self) -> None:
        index.sync(self.cfg)
        self.prompt("zebra with " + "ghp_" + "q" * 36)
        with open(shadowio.shadow_path(self.sb.state), encoding="utf-8") as handle:
            self.assertNotIn("ghp_", handle.read())

    def test_budget_exceeded_logs_nothing_and_does_not_fail(self) -> None:
        index.sync(self.cfg)
        self.shadow_config(budget_ms="0")
        self.assertIsNone(self.prompt("zebra stripes migration"))
        self.assertEqual(self.errors(), "")

    def test_ticket_pack_paths_are_logged_as_delivered_then_carried(self) -> None:
        self.disable_tickets()
        self.set_config('[jira]\nprefixes = ["ABC"]\n')
        self.write("tasks/ABC-7.md", "---\ntitle: T\n---\n## Notes\nzebra work\n")
        index.sync(config.load(self.env))
        self.prompt("about ABC-7 zebra")
        self.prompt("again ABC-7 zebra")
        first, second = self.entries()
        self.assertIn("tasks/ABC-7.md", first["delivered"])
        self.assertEqual(second["delivered"], [])
        self.assertIn("tasks/ABC-7.md", second["carried"])


if __name__ == "__main__":
    unittest.main()
