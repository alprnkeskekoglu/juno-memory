import hashlib
import json
import os
import re
import stat
import subprocess
import time
import unittest
from unittest import mock

from juno import cli, config
from juno.cmd import dashboard as cmd
from juno.core import dashboard, dashboard_collect, dashboard_grade, dashboard_html
from juno.core.dashboard_assets import JS, THEME_JS
from tests.support import PYTHON, REPO, SandboxCase, subproc

TZ = "Europe/Istanbul"


def local_epoch(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def visible(page: str) -> str:
    page = re.sub(r"<script>.*?</script>|<style>.*?</style>", "", page, flags=re.S)
    return re.sub(r'title="[^"]*"', "", page)


def tree_hash(root: str) -> str:
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                digest.update(os.path.relpath(path, root).encode() + b"\0" + handle.read())
    return digest.hexdigest()


class DashCase(SandboxCase):
    preset = "none"

    def setUp(self) -> None:
        super().setUp()
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = TZ
        time.tzset()
        self.addCleanup(self.restore_tz)
        self.now = local_epoch("2026-10-25 12:00:00")
        self.projects = os.path.join(self.sb.root, "projects")
        os.makedirs(os.path.join(self.projects, "p"))
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = self.projects
        self.sb.env["TZ"] = TZ
        os.makedirs(os.path.dirname(self.sb.config))
        with open(self.sb.config, "w", encoding="utf-8") as h:
            h.write(f'[language]\npreset = "{self.preset}"\n[jira]\nprefixes = ["ABC"]\n')
        self.write(
            "threads.md",
            "- [ABC-1] check the rollout 2026-10-20 <script>alert(1)</script> · since 2026-10-01\n"
            "- [ABC-2] ship it 2026-10-25 and 2026-12-01 · since 2026-10-24\n",
        )
        self.write(
            "backlog/p1-item.md",
            "---\ntitle: Fix the sync job today\nstatus: next\npriority: p1\n"
            "due: 2026-12-31\nadded: 2026-10-01\nupdated: 2026-10-01\n---\n\n## Task\nx\n",
        )
        self.write(
            "handoffs/a.md",
            "## 2026-10-25 09:00 · - · first card · aaaaaaaa\n\nRepos: -\n\n"
            "## 2026-10-25 11:00 · ABC-9 · second <b>card</b> · bbbbbbbb\n",
        )
        self.write("handoffs/b.md", "## 2026-10-25 11:00 · ABC-9 · second <b>card</b> · bbbbbbbb\n")
        self.write(
            "tasks/ABC-9.md", "---\nstatus: in-progress\ntitle: Nine\nrepos: [app]\n---\nbody\n"
        )
        self.write("ideas/idea-one.md", "---\nstatus: planned\n---\n\n## Idea\n\nTry the thing\n")

    def restore_tz(self) -> None:
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()

    def write(self, rel: str, text: str, root: str | None = None) -> str:
        path = os.path.join(root or self.sb.vault, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as h:
            h.write(text)
        return path

    def state_file(self, rel: str, text: str) -> str:
        return self.write(rel, text, self.sb.state)

    def sources(self) -> dashboard.Sources:
        return cmd.sources(config.load(self.sb.env))

    def data(self) -> dict:
        return dashboard_collect.collect(self.sources(), self.now)

    def page(self, data: dict | None = None) -> str:
        return dashboard_html.render(data or self.data(), self.sources().t)


class Collectors(DashCase):
    def test_today_collectors(self) -> None:
        t = dashboard_collect.collect_today(self.sources(), self.now)
        states = {d["date"]: d["state"] for i in t["threads"]["items"] for d in i["dates"]}
        self.assertEqual(
            (states["2026-10-20"], states["2026-10-25"], states["2026-12-01"]),
            ("past", "due", "future"),
        )
        self.assertEqual([c["seg"] for c in t["handoffs"]["cards"]], ["bbbbbbbb", "aaaaaaaa"])
        self.assertEqual((t["handoffs"]["total"], t["backlog"]["items"][0]["slug"]), (2, "p1-item"))

    def test_experiments_for_work(self) -> None:
        e = dashboard_collect.collect_experiments(self.sources(), self.now)
        self.assertEqual(e["tasks"]["items"][0]["key"], "ABC-9")
        self.assertEqual(e["ideas"]["items"][0]["title"], "Try the thing")
        self.assertEqual((e["recall"]["entries"], e["recall"]["recall"]), (0, None))

    def test_missing_sources_say_unavailable_in_the_page(self) -> None:
        os.unlink(os.path.join(self.sb.vault, "threads.md"))
        data = self.data()
        self.assertEqual(
            data["today"]["threads"], {"unavailable": "FileNotFoundError: threads.md not found"}
        )
        page = self.page(data)
        self.assertIn('title="FileNotFoundError: threads.md not found">no data</span>', page)
        self.assertIn("Thread list unreadable", page)
        self.assertNotIn("FileNotFoundError", visible(page))

    def test_missing_transcripts_dir_is_unavailable_not_zero(self) -> None:
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "nowhere")
        self.assertIn("transcripts dir not found", self.data()["cost"]["usage"]["unavailable"])

    def test_corrupt_sources_are_unavailable(self) -> None:
        self.state_file("status.json", "{broken")
        self.state_file(
            "cards/runs.jsonl",
            "garbage\n"
            + json.dumps({"t": self.now - 100, "status": "failed", "error": "<i>boom</i>"})
            + "\n",
        )
        data = self.data()
        self.assertIn("JSONDecodeError", data["cost"]["quota"]["unavailable"])
        self.assertEqual(data["health"]["runs"]["bad_lines"], 1)
        page = self.page(data)
        self.assertIn("1 failed today · 1 in 7 days", page)
        self.assertNotIn("boom", page)
        self.assertEqual(data["health"]["verdict"]["level"], "bad")

    def test_quota_reads_known_fields_and_tolerates_absence(self) -> None:
        self.state_file(
            "status.json",
            json.dumps({"rate_limits": [{"five_hour": {"percent": 40}}, {"seven_day": 12}]}),
        )
        self.assertEqual(
            dashboard_collect.cost_quota(self.sources())["windows"],
            {"five_hour": {"percent": 40}, "seven_day": 12},
        )
        self.state_file("status.json", json.dumps({"updated": "x"}))
        with self.assertRaises(KeyError):
            dashboard_collect.cost_quota(self.sources())

    def test_health_collectors_and_queues(self) -> None:
        self.state_file(
            "hook-errors.log", "2026-10-25T10:00:00+0300 x boom\n2026-10-25T11:00:00+0300 y bang\n"
        )
        item = {
            "v": 1,
            "items": [
                {"id": "b123456", "status": "pending"},
                {"id": "b222222", "status": "accepted"},
            ],
        }
        self.state_file("cards/backlog-suggestions.json", json.dumps(item))
        self.state_file("cards/candidates.json", "{broken")
        h = dashboard_collect.collect_health(self.sources(), self.now)
        self.assertEqual((h["hook_errors"]["count"], h["hook_errors"]["last_24h"]), (2, 2))
        self.assertEqual(
            (h["queues"]["backlog_suggestions"], h["queues"]["thread_candidates"]), (1, 0)
        )
        self.assertIn("unavailable", h["queues"]["rule_candidates"])
        self.assertIn("index.sqlite3 not found", h["coverage"]["index"]["unavailable"])
        self.assertIn("stale.json not found", h["coverage"]["stale"]["unavailable"])

    def test_coverage_counts_index_and_stale_files(self) -> None:
        import sqlite3

        self.write("knowledge/a.md", "# A\n")
        self.write("projects/b.md", "# B\n")
        self.write("knowledge/c.md", "# C\n")
        os.makedirs(self.sb.state)
        conn = sqlite3.connect(os.path.join(self.sb.state, "index.sqlite3"))
        conn.execute("CREATE TABLE files (path TEXT)")
        conn.executemany("INSERT INTO files VALUES (?)", [("a",), ("b",)])
        conn.commit()
        conn.close()
        cov = dashboard_collect.collect_health(self.sources(), self.now)["coverage"]
        self.assertEqual(cov["index"]["indexed"], 2)
        self.assertGreaterEqual(cov["index"]["vault"], 3)
        self.state_file(
            "stale.json",
            json.dumps(
                {
                    "v": 1,
                    "generated": 5,
                    "files": {
                        "knowledge/a.md": {"stale": True},
                        "knowledge/c.md": {"stale": False},
                        "knowledge/gone.md": {"stale": True},
                    },
                }
            ),
        )
        self.assertEqual(
            dashboard_collect.coverage_stale(self.sources()),
            {"stale": 1, "total": 3, "generated": 5},
        )
        self.state_file("stale.json", "{broken")
        with self.assertRaises(ValueError):
            dashboard_collect.coverage_stale(self.sources())

    def test_juno_spend_against_the_budget(self) -> None:
        self.state_file(
            "cards/runs.jsonl",
            json.dumps({"t": self.now, "status": "ok", "cost_usd": 0.3})
            + "\n"
            + json.dumps({"t": self.now - 60, "status": "ok", "cost_usd": 0.2})
            + "\n",
        )
        got = dashboard_collect.cost_juno(self.sources(), self.now)
        self.assertEqual((got["cards"]["spend"]["2026-10-25"], got["cards"]["budget"]), (0.5, 2.0))

    def test_eval_results_are_cached_for_an_hour(self) -> None:
        fake = mock.Mock()
        fake.card_ab.return_value = {"segments": 0, "arms": {}}
        fake.recall_proxy.return_value = {"with_evidence": 0, "misses": list(range(9))}
        with mock.patch.object(dashboard_collect, "eval_module", return_value=fake):
            src = self.sources()
            dashboard_collect.experiments_ab(src, self.now)
            dashboard_collect.experiments_ab(src, self.now + 3599)
            self.assertEqual(fake.card_ab.call_count, 1)
            dashboard_collect.experiments_ab(src, self.now + 3601)
            self.assertEqual(fake.card_ab.call_count, 2)
            first = dashboard_collect.experiments_recall(src, self.now)
            self.assertEqual(len(first["misses"]), dashboard_collect.MISSES_KEPT)
        path = os.path.join(self.sb.state, "dashboard", "recall.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.state_file("dashboard/recall.json", "{broken")
        with mock.patch.object(dashboard_collect, "eval_module", return_value=fake):
            dashboard_collect.experiments_recall(src, self.now)
            self.assertEqual(fake.recall_proxy.call_count, 2)

    def test_gate_record_is_read(self) -> None:
        self.state_file("eval/set-1.json", "{}")
        self.state_file("eval/set-1.json.sha256", "x")
        self.state_file(
            "eval/last-gate.json",
            json.dumps({"passed": False, "reasons": ["floor"], "set": "set-1.json"}),
        )
        gate = dashboard_collect.experiments_gate(self.sources())
        self.assertEqual(gate["sets"], [{"name": "set-1.json", "frozen": True}])
        self.assertIn("floor", self.page(self.data()))


class Page(DashCase):
    def test_page_contract(self) -> None:
        page = self.page()
        csp = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"
        self.assertIn(f'content="{csp}"', page)
        self.assertNotIn("connect-src", page)
        self.assertIn('<html lang="en">', page)
        self.assertIn("updated 12:00 · next ~14:00", page)
        self.assertIn("API-price estimate", page)
        self.assertEqual(
            re.findall(r'<span class="st">(.*?)</span>', page),
            ["Threads", "Recent cards", "Health", "Cost", "Memory"],
        )
        for forbidden in (
            "http://",
            "https://",
            "<link",
            "<img",
            "@import",
            "url(",
            "fetch(",
            "XMLHttpRequest",
            "innerHTML",
        ):
            self.assertNotIn(forbidden, page)
        self.assertEqual(page.count("<script>"), 2)
        self.assertTrue(page.endswith("\n"))
        for word in ("alarm", "mentee", "Saga", "saga"):
            self.assertNotIn(word, page)

    def test_no_alarm_or_mentee_panels_in_the_data(self) -> None:
        data = self.data()
        self.assertEqual(
            sorted(data["health"]), ["coverage", "hook_errors", "queues", "rows", "runs", "verdict"]
        )
        self.assertNotIn("needs", data)
        self.assertNotIn("alarms", data["cost"]["juno"])

    def test_page_is_complete_without_javascript(self) -> None:
        page = self.page()
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        for text in ("check the rollout", "Fix the sync job", "second", "ABC-1"):
            self.assertIn(text, page)
            self.assertFalse(any(text in s for s in scripts), text)
        self.assertEqual(scripts, [THEME_JS, JS])
        self.assertEqual(page.count('id="q"'), 1)

    def test_vault_text_is_escaped(self) -> None:
        page = self.page()
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<b>card</b>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_every_hostile_string_is_escaped(self) -> None:
        evil = '"><img src=x onerror=1>'
        data = self.data()
        data["today"]["threads"] = {
            "items": [
                {
                    "line": 1,
                    "tag": evil,
                    "tags": [evil],
                    "group": evil,
                    "text": evil,
                    "age": 30,
                    "since": None,
                    "dates": [],
                    "due": "2026-10-20",
                    "due_state": "past",
                }
            ]
        }
        data["today"]["backlog"]["items"] = [
            {
                "slug": evil,
                "title": evil,
                "status": "doing",
                "priority": "p1",
                "due": None,
                "overdue": False,
                "urgent": True,
            }
        ]
        data["today"]["handoffs"] = {
            "cards": [
                {"time": "2026-10-25 09:00", "key": evil, "label": evil, "seg": "x", "file": evil}
            ],
            "total": 1,
        }
        data["health"]["rows"] = [
            {"id": "x", "label": evil, "level": "red", "detail": evil, "fix": evil}
        ]
        data["experiments"]["gate"] = {
            "last": {"passed": False, "reasons": [evil], "set": evil},
            "sets": [],
        }
        data["experiments"]["ideas"] = {"items": [{"id": evil, "title": evil}]}
        page = self.page(data)
        self.assertNotIn("<img", page)
        self.assertNotIn("onerror=1>", page)
        self.assertGreater(page.count("&lt;img src=x onerror=1&gt;"), 8)

    def test_open_threads_are_grouped_with_age_badges_and_overdue_first(self) -> None:
        self.write(
            "threads.md",
            "- [app] gate failed 2026-10-25; rerun ~10-28 · since 2026-10-19\n"
            "- [ABC-3] sweep 2026-10-26 · since 2026-10-25\n- [nova] no date · since 2026-10-22\n"
            "- [x] old 2026-10-20 · since 2026-10-01\n",
        )
        work = self.page().split("Threads", 1)[1].split("Recent cards", 1)[0]
        self.assertEqual(
            re.findall(r'<div class="ghd"><b>(.*?)</b>', work), ["x", "app", "nova", "ABC-3"]
        )
        self.assertIn('<span class="bd warn">oldest 24 days</span>', work)
        self.assertIn('<span class="bd bad">overdue</span>', work)
        self.assertIn('<span class="bd warn">tomorrow</span>', work)
        self.assertIn('<span class="muted">28 Oct</span>', work)

    def test_todo_list_sources_commands_and_order(self) -> None:
        self.state_file(
            "cards/backlog-suggestions.json",
            json.dumps({"v": 1, "items": [{"id": "b123456", "status": "pending"}]}),
        )
        todo = self.page().split('<section class="todo">', 1)[1].split("</section>", 1)[0]
        order = [
            "Fix the sync job today",
            "1 backlog suggestions waiting",
            "stale-memory scan never ran",
        ]
        positions = [todo.index(t) for t in order]
        self.assertEqual(positions, sorted(positions))
        for cmd_text in ("juno backlog show p1-item", "juno backlog list"):
            self.assertIn(f'<code class="cmd">{cmd_text}</code>', todo)

    def test_verdict_levels(self) -> None:
        def row(level: str) -> dict:
            return {"level": level}

        self.assertEqual(dashboard_grade.verdict([row("green"), row("na")])["level"], "good")
        self.assertEqual(dashboard_grade.verdict([row("yellow")])["level"], "warn")
        self.assertEqual(dashboard_grade.verdict([row("yellow"), row("red")])["level"], "bad")

    def test_a_malformed_block_degrades_one_row_only(self) -> None:
        data = self.data()
        data["health"]["runs"] = {"days": []}
        rows = {r["id"]: r for r in dashboard_grade.grade(data, self.sources().t)}
        self.assertEqual(rows["runs"]["level"], "na")
        self.assertNotEqual(rows["hook_errors"]["level"], "na")

    def test_cost_section_with_transcripts_and_over_budget(self) -> None:
        row = {
            "type": "assistant",
            "timestamp": "2026-10-25T08:00:00Z",
            "sessionId": "s",
            "cwd": "/w",
            "message": {
                "id": "m1",
                "model": "claude-opus-4-7",
                "usage": {"input_tokens": 1000000, "output_tokens": 0},
            },
        }
        self.write("p/a.jsonl", json.dumps(row) + "\n", self.projects)
        self.state_file(
            "cards/runs.jsonl", json.dumps({"t": self.now, "status": "ok", "cost_usd": 3.0}) + "\n"
        )
        page = self.page()
        self.assertIn("$5.00", page)
        self.assertIn('class="stat over"', page)
        self.assertTrue(os.path.isfile(os.path.join(self.sb.state, "dashboard", "usage.json")))


class Turkish(DashCase):
    preset = "tr"

    def test_tr_preset_keeps_the_v1_strings(self) -> None:
        page = self.page()
        self.assertIn('<html lang="tr">', page)
        self.assertIn("güncellendi 12:00 · sonraki ~14:00", page)
        self.assertEqual(
            re.findall(r'<span class="st">(.*?)</span>', page),
            ["Thread&#x27;ler", "Son kartlar", "Sağlık", "Maliyet", "Hafıza"],
        )
        self.assertIn("API fiyatıyla tahmin; Max kotan değil", page)
        self.assertIn("Yapılacaklar", page)


class Cli(DashCase):
    def test_writes_the_page_atomically_with_mode_0600_and_makes_no_vault_writes(self) -> None:
        before = tree_hash(self.sb.vault)
        code, out = self.sb.cli("dashboard")
        self.assertEqual(code, 0)
        path = out.strip()
        self.assertEqual(path, os.path.join(self.sb.state, "dashboard", "index.html"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(tree_hash(self.sb.vault), before)
        written = {
            os.path.relpath(os.path.join(b, f), self.sb.state)
            for b, _, fs in os.walk(self.sb.state)
            for f in fs
        }
        self.assertTrue(all(w.startswith("dashboard" + os.sep) for w in written), written)
        with open(path, encoding="utf-8") as h:
            self.assertIn("<title>Juno</title>", h.read())
        self.assertEqual(
            [n for n in os.listdir(os.path.dirname(path)) if n.startswith(".juno-tmp-")], []
        )

    def test_out_and_json(self) -> None:
        target = os.path.join(self.sb.root, "out", "page.html")
        code, out = self.sb.cli("dashboard", "--out", target)
        self.assertEqual((code, out.strip()), (0, target))
        self.assertTrue(os.path.isfile(target))
        code, out = self.sb.cli("dashboard", "--json")
        self.assertEqual(code, 0)
        self.assertIn("today_date", json.loads(out))

    def test_out_inside_the_vault_is_refused(self) -> None:
        before = tree_hash(self.sb.vault)
        code, _ = self.sb.cli("dashboard", "--out", os.path.join(self.sb.vault, "x.html"))
        self.assertEqual(code, 2)
        self.assertEqual(tree_hash(self.sb.vault), before)

    def test_open_runs_the_viewer_only_when_asked(self) -> None:
        with (
            mock.patch.object(cmd.subprocess, "run") as run,
            mock.patch.object(cmd, "opener", return_value="/bin/viewer"),
        ):
            self.sb.cli("dashboard")
            run.assert_not_called()
            self.sb.cli("dashboard", "--open")
            self.assertEqual(run.call_args[0][0][0], "/bin/viewer")

    def test_verb_is_registered(self) -> None:
        self.assertIn("dashboard", cli.VERBS)

    @subproc
    def test_no_network_modules_are_imported(self) -> None:
        code = (
            "import sys; sys.path.insert(0, %r); from juno import cli; cli.main(['dashboard'])\n"
            "banned = {'socket','ssl','http','urllib','smtplib','ftplib'}\n"
            "print(sorted(banned & set(sys.modules)))" % REPO
        )
        done = subprocess.run(
            [PYTHON, "-c", code],
            capture_output=True,
            text=True,
            env=self.sb.env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        self.assertEqual(done.stdout.strip().splitlines()[-1], "[]", done.stderr)


if __name__ == "__main__":
    unittest.main()
