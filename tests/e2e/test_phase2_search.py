import json
import os
import subprocess
import time
import unittest
import uuid
from pathlib import Path
from typing import Any

from tests.e2e import harness
from tests.e2e.harness import Run
from tests.support import Sandbox, e2e

DAY = 86400


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.projects = os.path.join(self.sb.root, "claude-projects")
        self.roots = os.path.join(self.sb.root, "repos")
        self.work = os.path.join(self.sb.root, "work")
        for directory in (self.projects, self.roots, self.work):
            os.makedirs(directory)
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = self.projects
        self.sb.env["JUNO_REPO_ROOTS"] = self.roots

    def put(self, rel: str, text: str) -> str:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def config(self, toml: str) -> None:
        os.makedirs(os.path.dirname(self.sb.config), exist_ok=True)
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n" + toml + "\n")

    def juno(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return harness.shim(self.sb, *argv)

    def juno_json(self, *argv: str) -> dict[str, Any]:
        done = self.juno(*argv, "--json")
        self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
        data: dict[str, Any] = json.loads(done.stdout)
        return data

    def shadow_entries(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "shadow.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def claude(self, prompt: str, interactive: bool = True) -> Run:
        harness.require_claude(self)
        env = harness.claude_env(self.sb, interactive)
        env["JUNO_REPO_ROOTS"] = self.roots
        env["JUNO_CLAUDE_PROJECTS"] = self.projects
        run = harness.run_claude(prompt, env, cwd=self.work)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        self.assertEqual(harness.juno_hook_failures(run), [])
        for response in run.hook_responses:
            self.assertIn(response.get("exit_code"), (0, None), response)
            text = harness.context_of(response)
            if text is not None:
                self.assertLessEqual(len(text), harness.HOOK_CAP)
        return run


@e2e
class SearchThroughTheShim(Case):
    def test_typo_kinds_private_retired_and_archive(self) -> None:
        for i in range(6):
            self.put(
                f"handoffs/h{i}.md",
                f"# H\n\n## 2026-10-25 23:2{i} · - · c · aaaaaaa{i}\nholdout holdout card {i}\n",
            )
        self.put("knowledge/k.md", "# Know\n\nholdout knowledge about deployment\n")
        self.put(
            "notes/private.md", "---\nvisibility: private\n---\n# P\n\nholdout privatesecret\n"
        )
        self.put("notes/old.md", "# Old\n\nholdout retiredterm\n")
        self.put("notes/new.md", "---\nsupersedes: notes/old.md\n---\n# New\n\nholdout newer\n")
        self.put("archive/handoffs/2026-01.md", "# Archived\n\nholdout archivedterm\n")

        typo = self.juno("search", "holdotu", "--limit", "20")
        self.assertEqual(typo.returncode, 0, typo.stderr)
        self.assertIn('(searched "holdout" for "holdotu")', typo.stdout)
        self.assertIn("knowledge/k.md", typo.stdout)

        data = self.juno_json("search", "holdout", "--limit", "4")
        kinds = [r["relpath"].split("/")[0] for r in data["results"]]
        self.assertEqual(len(kinds), 4)
        self.assertLessEqual(kinds.count("handoffs"), 2)

        everything = self.juno("search", "holdout", "--limit", "50").stdout
        for hidden in ("private.md", "notes/old.md", "archive/"):
            self.assertNotIn(hidden, everything)
        self.assertIn("notes/new.md", everything)

        archived = self.juno("search", "archivedterm", "--archive")
        self.assertIn("archive/handoffs/2026-01.md [archive]", archived.stdout)
        self.assertEqual(self.juno("search", "archivedterm").stdout.strip(), "no results")

    def test_search_leaves_the_vault_alone(self) -> None:
        self.put("notes/a.md", "# A\n\nuniqueterm\n")
        before = {p: os.stat(p).st_mtime_ns for p in Path(self.sb.vault).rglob("*") if p.is_file()}
        self.assertEqual(self.juno("search", "uniqueterm").returncode, 0)
        after = {p: os.stat(p).st_mtime_ns for p in Path(self.sb.vault).rglob("*") if p.is_file()}
        self.assertEqual(before, after)


@e2e
class SearchSinceUntil(Case):
    def test_an_older_card_is_excluded_and_a_history_passage_is_flagged(self) -> None:
        self.put(
            "handoffs/old.md", "# H\n\n## 2026-01-05 09:00 · - · c · aaaaaaa1\nwindowterm early\n"
        )
        self.put(
            "handoffs/new.md", "# H\n\n## 2026-10-20 09:00 · - · c · aaaaaaa2\nwindowterm late\n"
        )
        self.put(
            "decisions/t.md",
            "---\nupdated: 2026-10-25\n---\n# T\n\n## Current\n\n- windowterm live\n\n"
            "## History\n\n- retiredterm dropped · superseded 2026-10-26\n",
        )
        windowed = self.juno("search", "windowterm", "--since", "2026-10-01", "--limit", "10")
        self.assertEqual(windowed.returncode, 0, windowed.stderr)
        self.assertNotIn("handoffs/old.md", windowed.stdout)
        self.assertIn("handoffs/new.md", windowed.stdout)
        self.assertIn("decisions/t.md · T > Current · 2026-10-25", windowed.stdout)
        hist = self.juno("search", "retiredterm")
        self.assertIn("decisions/t.md [superseded] · T > History · 2026-10-26", hist.stdout)
        early = self.juno("search", "windowterm", "--until", "2026-01-05")
        self.assertIn("handoffs/old.md", early.stdout)
        self.assertNotIn("handoffs/new.md", early.stdout)
        bad = self.juno("search", "windowterm", "--since", "last-week")
        self.assertEqual(bad.returncode, 2)


@e2e
class ShadowLogOfASession(Case):
    def test_prompt_is_logged_with_suggestions_once_the_index_exists(self) -> None:
        word = "zq" + uuid.uuid4().hex[:10]
        self.put("knowledge/shadowed.md", f"# Shadowed\n\n{word} is the migration codeword\n")
        self.assertEqual(self.juno("search", word).returncode, 0)
        self.claude(f"Reply with the single word ok. Codeword {word}")
        entries = self.shadow_entries()
        self.assertEqual(len(entries), 1, entries)
        entry = entries[0]
        self.assertIn(word, entry["prompt"])
        self.assertIn("knowledge/shadowed.md", [s["path"] for s in entry["suggestions"]])
        self.assertNotIn("arm", entry)
        self.assertEqual(len(entry["segment"]), 36)

    def test_headless_session_without_the_override_logs_nothing(self) -> None:
        word = "zq" + uuid.uuid4().hex[:10]
        self.put("knowledge/shadowed.md", f"# Shadowed\n\n{word} is the migration codeword\n")
        self.juno("search", word)
        self.claude(f"Reply with the single word ok. Codeword {word}", interactive=False)
        self.assertEqual(self.shadow_entries(), [])


@e2e
class StaleFlagsAnOldGotcha(Case):
    def test_old_profile_is_flagged_and_cached(self) -> None:
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 200 * DAY))
        self.put(
            "projects/app.md",
            f"---\nrepo: app\nupdated: {old}\n---\n## Gotchas\n- restart the worker after a deploy\n",
        )
        self.put("projects/fresh.md", "---\nrepo: fresh\n---\n## Gotchas\n- nothing special\n")
        done = self.juno("stale")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"projects/app.md · baseline {old} (updated) · 200 days old", done.stdout)
        self.assertNotIn("projects/fresh.md ·", done.stdout)
        cache = json.loads(Path(self.sb.state, "stale.json").read_text())
        self.assertIs(cache["files"]["projects/app.md"]["stale"], True)
        self.assertIs(cache["files"]["projects/fresh.md"]["stale"], False)


@e2e
class LinksSwitch(Case):
    def setUp(self) -> None:
        super().setUp()
        self.config('[jira]\nprefixes = ["ABC"]')
        self.put(
            "tasks/ABC-1.md",
            "---\ntitle: Fix refunds\nstatus: open\n---\n## Notes\nSee [[notes/retry-plan]].\n",
        )
        self.put("notes/retry-plan.md", "# Retry plan\n\nretry with backoff\n")
        self.put("handoffs/old.md", "# Old\n\nA card about [[ABC-1]].\n")
        for i in range(6):
            self.put(f"notes/extra{i}.md", f"# Extra {i}\n\nsee [[ABC-1]]\n")
        self.assertEqual(self.juno("search", "retry").returncode, 0)

    def arm(self, fraction: float) -> None:
        directory = os.path.join(self.sb.state, "arms")
        os.makedirs(directory, exist_ok=True)
        body = {
            "name": "linked",
            "salt": "e2e",
            "status": "running",
            "started": 1,
            "prereg": {"assignment": {"fraction": fraction}},
        }
        Path(directory, "linked.json").write_text(json.dumps(body))

    def pack_events(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        directory = os.path.join(self.sb.state, "ledgers")
        for name in sorted(os.listdir(directory)):
            lines = Path(directory, name).read_text().splitlines()
            found += [r for r in map(json.loads, lines) if r.get("ev") == "pack"]
        return found

    def pack_text(self, run: Run) -> str:
        found = []
        for response in run.hook_responses:
            if response.get("hook_event") == "UserPromptSubmit":
                text = harness.context_of(response)
                if text:
                    found.append(text)
        return "\n".join(found)

    def test_linked_block_follows_the_arm(self) -> None:
        none = self.claude("Reply with the single word ok. Ticket ABC-1")
        self.assertIn("ABC-1", self.pack_text(none))
        self.assertNotIn("[Linked", self.pack_text(none))

        self.arm(1.0)
        on = self.claude("Reply with the single word ok. Ticket ABC-1 again")
        text = self.pack_text(on)
        self.assertIn("[Linked · wikilinks]", text)
        block = text.split("[Linked · wikilinks]\n", 1)[1].split("\n[", 1)[0]
        self.assertNotIn("handoffs/", block)
        self.assertLessEqual(len(block.splitlines()), 5)
        self.assertIn("notes/retry-plan.md", block)
        links = json.loads(Path(self.sb.state, "links.json").read_text())
        self.assertEqual(links["files"]["tasks/ABC-1.md"]["links"][0][1], "notes/retry-plan")

        self.arm(0.0)
        off = self.claude("Reply with the single word ok. Ticket ABC-1 once more")
        self.assertNotIn("[Linked", self.pack_text(off))
        events = self.pack_events()
        self.assertEqual(len(events), 3, events)
        for event in events:
            self.assertTrue(0 < len(event["nb"]) <= 5, event)
            self.assertNotIn("handoffs/old.md", event["nb"])


@e2e
class EvalAndDashboardPanels(Case):
    def entry(self, i: int, hard: bool) -> dict[str, Any]:
        return {
            "id": f"e{i}{'h' if hard else 'u'}",
            "ts": 1,
            "segment": "s",
            "prompt": f"qqqxyz {i}" if hard else f"zebra stripes {i}",
            "suggestions": []
            if hard
            else [{"path": "notes/zebra.md", "heading": "z", "score": 5.0}],
            "label": None if hard else "useful",
            "prefill": None,
            "forbidden": [],
            "hard_negative": hard,
        }

    def test_eval_runs_on_a_synthetic_log_and_the_dashboard_shows_it(self) -> None:
        self.put("notes/zebra.md", "# zebra\n\nzebra stripes migration notes\n")
        self.put("knowledge/k.md", "# K\n\nsome knowledge\n")
        state = Path(self.sb.state)
        now = int(time.time())
        lines = []
        for i in range(8):
            suggestions = (
                [{"path": "notes/zebra.md", "heading": "z", "score": 5.0}] if i % 2 else []
            )
            lines.append(
                {
                    "ts": now - 100 + i,
                    "segment": f"seg{i % 2}",
                    "prompt": f"zebra stripes {i}",
                    "suggestions": suggestions,
                }
            )
        state.mkdir(parents=True, exist_ok=True)
        (state / "shadow.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))

        sampled = self.juno("eval", "sample", "--n", "4")
        self.assertEqual(sampled.returncode, 0, sampled.stderr)
        self.assertEqual(sampled.stdout.strip(), str(state / "eval" / "set-1.json"))

        entries = [self.entry(i, False) for i in range(5)] + [
            self.entry(i, True) for i in range(20)
        ]
        path = state / "eval" / "set-2.json"
        path.write_text(json.dumps({"v": 1, "seed": 0, "entries": entries}))
        self.assertEqual(self.juno("eval", "freeze", str(path)).returncode, 0)
        floor = self.juno("eval", "floor", str(path))
        self.assertEqual(floor.returncode, 0, floor.stderr)
        gate = self.juno("eval", "gate", str(path), "--floor", floor.stdout.strip())
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.assertIn("gate: PASS", gate.stdout)

        recall = self.juno("eval", "recall")
        self.assertIn("shadow entries: 8", recall.stdout)
        self.assertEqual(self.juno("eval", "ab").returncode, 0)

        self.put("projects/app.md", "---\nrepo: app\nupdated: 2020-01-01\n---\n## Gotchas\n- x\n")
        self.assertEqual(self.juno("stale").returncode, 0)
        data = self.juno_json("dashboard")
        coverage = data["health"]["coverage"]
        self.assertGreater(coverage["index"]["indexed"], 1)
        self.assertEqual(coverage["stale"]["stale"], 1)
        experiments = data["experiments"]
        self.assertEqual(experiments["recall"]["entries"], 8)
        self.assertNotIn("unavailable", experiments["recall"])
        self.assertNotIn("unavailable", experiments["card_ab"])
        self.assertIs(experiments["gate"]["last"]["passed"], True)
        self.assertEqual(
            {s["name"] for s in experiments["gate"]["sets"]}, {"set-1.json", "set-2.json"}
        )


if __name__ == "__main__":
    unittest.main()
