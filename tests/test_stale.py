import contextlib
import datetime
import io
import json
import os
import subprocess
import time
import unittest
from typing import Any
from unittest import mock

from juno import cli, config
from juno.adapters import stalescan, store
from juno.adapters.vaultview import DiskVault
from juno.core import packs, stale
from juno.core.packs import Caps
from tests.support.hooks import HookCase

TODAY = datetime.date.today()


def days_ago(n: int) -> str:
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def knowledge(
    updated: str,
    body: str,
    repos: str = "[hb]",
    verified: str | None = None,
    title: str = "Journey tables",
) -> str:
    extra = f"verified: {verified}\n" if verified else ""
    return (
        f"---\ntitle: {title}\nrepos: {repos}\ntickets: [ABC-1]\nupdated: {updated}\n"
        f"{extra}sources: [x]\n---\n{body}\n"
    )


class Base(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("hb", "main")
        self.cfg = config.load(self.env)

    def commit(self, rel: str, date: str, text: str = "x") -> None:
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": self.sb.root,
            "GIT_AUTHOR_DATE": date + "T12:00:00",
            "GIT_COMMITTER_DATE": date + "T12:00:00",
        }
        for args in (
            ["add", rel],
            [
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.com",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "c",
            ],
        ):
            subprocess.run(
                ["git", "-C", self.repo, *args], check=True, capture_output=True, env=env
            )

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), self.env)
        return code, buffer.getvalue()

    def stale(self, *args: str) -> dict[str, Any]:
        code, out = self.run_cli("stale", "--json", *args)
        self.assertEqual(code, 0, out)
        report: dict[str, Any] = json.loads(out)
        return report

    def entry(self, report: dict[str, Any], relpath: str) -> dict[str, Any]:
        found: dict[str, Any] = next(e for e in report["entries"] if e["file"] == relpath)
        return found

    def retired(self) -> None:
        os.makedirs(self.sb.state, exist_ok=True)
        with open(os.path.join(self.sb.state, "retired.json"), "w", encoding="utf-8") as handle:
            json.dump({"v": 1, "generated": "x", "retired": []}, handle)

    def tree(self, root: str) -> dict[str, tuple[int, int, str]]:
        found = {}
        for directory, _, names in os.walk(root):
            for name in names:
                path = os.path.join(directory, name)
                st = os.stat(path)
                found[path] = (st.st_size, st.st_mtime_ns, store.read_text(path))
        return found


class Signals(Base):
    def test_age_only(self) -> None:
        self.write("knowledge/old.md", knowledge(days_ago(90), "No paths here."))
        self.write("knowledge/new.md", knowledge(days_ago(5), "No paths here."))
        report = self.stale()
        old, new = self.entry(report, "knowledge/old.md"), self.entry(report, "knowledge/new.md")
        self.assertTrue(old["stale"] and old["aged"])
        self.assertEqual(old["age_days"], 90)
        self.assertEqual(old["baseline_source"], "updated")
        self.assertFalse(new["stale"])
        self.assertFalse(self.entry(self.stale("--days", "100"), "knowledge/old.md")["stale"])

    def test_old_gotchas_line_is_flagged_in_a_profile(self) -> None:
        self.write(
            "projects/hb.md",
            f"---\nrepo: hb\nupdated: {days_ago(120)}\n---\n## Gotchas\n- the cache needs a restart\n",
        )
        entry = self.entry(self.stale(), "projects/hb.md")
        self.assertEqual((entry["kind"], entry["stale"], entry["age_days"]), ("profile", True, 120))

    def test_drift_detected(self) -> None:
        self.commit("app/Enums/Types.php", days_ago(40))
        self.commit("app/Enums/Types.php", days_ago(3))
        self.commit("app/Enums/Types.php", days_ago(2))
        self.write(
            "knowledge/k.md",
            knowledge(days_ago(10), "See `hb/app/Enums/Types.php` and `app/Enums/`."),
        )
        entry = self.entry(self.stale(), "knowledge/k.md")
        self.assertTrue(entry["stale"])
        self.assertFalse(entry["aged"])
        self.assertEqual(
            entry["drift"],
            [
                {"repo": "hb", "path": "app/Enums/Types.php", "commits": 2},
                {"repo": "hb", "path": "app/Enums/", "commits": 2},
            ],
        )
        _, text = self.run_cli("stale")
        self.assertIn(f"drift: hb app/Enums/Types.php (2 commits since {days_ago(10)})", text)

    def test_verified_newer_than_commit_is_not_drift(self) -> None:
        self.commit("config/eligibility.php", days_ago(20))
        self.write(
            "knowledge/k.md",
            knowledge(days_ago(50), "`config/eligibility.php`", verified=days_ago(10)),
        )
        entry = self.entry(self.stale(), "knowledge/k.md")
        self.assertEqual(entry["baseline"], days_ago(10))
        self.assertEqual(entry["baseline_source"], "verified")
        self.assertEqual(entry["drift"], [])
        self.assertFalse(entry["stale"])

    def test_same_day_commit_is_not_drift(self) -> None:
        self.commit("a/b.go", days_ago(10))
        self.write("knowledge/k.md", knowledge(days_ago(10), "`a/b.go`"))
        self.assertEqual(self.entry(self.stale(), "knowledge/k.md")["drift"], [])

    def test_drift_detected_under_the_second_root(self) -> None:
        self.repo = self.make_repo("maestro", "main", self.extra)
        self.commit("skills/plan.md", days_ago(2))
        self.write("knowledge/a.md", knowledge(days_ago(10), "`skills/plan.md`", repos="[maestro]"))
        self.write("knowledge/b.md", knowledge(days_ago(10), "`maestro/skills/plan.md`"))
        report = self.stale()
        for rel in ("knowledge/a.md", "knowledge/b.md"):
            entry = self.entry(report, rel)
            self.assertEqual(
                entry["drift"], [{"repo": "maestro", "path": "skills/plan.md", "commits": 1}], rel
            )
            self.assertEqual(entry["missing_repos"], [], rel)

    def test_missing_repo_and_path_skipped(self) -> None:
        self.commit("real/file.go", days_ago(5))
        body = "`ghost/x/y.go` `real/gone.go` `nope.md` `real/file.go`"
        self.write("knowledge/k.md", knowledge(days_ago(10), body, repos="[hb, ghost]"))
        entry = self.entry(self.stale(), "knowledge/k.md")
        self.assertEqual(entry["missing_repos"], ["ghost"])
        self.assertEqual(entry["missing_paths"], ["ghost/x/y.go", "real/gone.go", "nope.md"])
        self.assertEqual([d["path"] for d in entry["drift"]], ["real/file.go"])

    def test_non_path_tokens_ignored(self) -> None:
        body = (
            "`make up` `Carbon::parse()` `style->$.formData` `isActive` `/etc/passwd` `../hb/x.go` "
            "`~/projects/hb` `https://x.io/a`\n```\n`fenced/x.go`\n```\n"
        )
        self.assertEqual(stale.path_tokens(body), [])
        self.assertEqual(
            stale.path_tokens("`internal/apierror/utils.go`, `architect/Models/`."),
            ["internal/apierror/utils.go", "architect/Models/"],
        )

    def test_option_like_tokens_dropped(self) -> None:
        body = "`--output=/x` `-c` `-rf` `--pre=x.go` `-x/y.go` `-` `--` `app/x.php`"
        self.assertEqual(stale.path_tokens(body), ["app/x.php"])

    def test_profile_uses_repo_field_and_mtime_fallback(self) -> None:
        self.commit("architect/Models/M.php", days_ago(1))
        path = self.write(
            "projects/hb.md",
            "---\nrepo: hb\npath: /elsewhere/hb\n---\n## Gotchas\n- `architect/Models/`\n",
        )
        old = (datetime.datetime.now() - datetime.timedelta(days=10)).timestamp()
        os.utime(path, (old, old))
        entry = self.entry(self.stale(), "projects/hb.md")
        self.assertEqual(entry["kind"], "profile")
        self.assertEqual(entry["baseline_source"], "mtime")
        self.assertEqual(
            entry["drift"], [{"repo": "hb", "path": "architect/Models/", "commits": 1}]
        )

    def test_top_level_directory_too_broad_to_count(self) -> None:
        self.commit("architect/Models/M.php", days_ago(1))
        self.write("knowledge/k.md", knowledge(days_ago(10), "`architect/` `hb/architect/`"))
        entry = self.entry(self.stale(), "knowledge/k.md")
        self.assertEqual((entry["drift"], entry["missing_paths"], entry["stale"]), ([], [], False))

    def test_archived_and_private_skipped(self) -> None:
        self.write(
            "knowledge/a.md",
            knowledge(days_ago(300), "x").replace("title:", "status: archived\ntitle:"),
        )
        self.write(
            "knowledge/p.md",
            knowledge(days_ago(300), "x").replace("title:", "visibility: private\ntitle:"),
        )
        self.assertEqual(self.stale()["entries"], [])


class CacheAndVault(Base):
    def test_cache_in_state_and_vault_untouched(self) -> None:
        self.commit("app/x.php", days_ago(1))
        self.write("knowledge/k.md", knowledge(days_ago(90), "`app/x.php`"))
        before = self.tree(self.sb.vault)
        status = ["git", "-C", self.repo, "status", "--porcelain"]
        repo_before = subprocess.run(status, capture_output=True, text=True).stdout
        report = self.stale()
        self.assertEqual(self.tree(self.sb.vault), before)
        self.assertEqual(report["cache"], os.path.join(self.sb.state, "stale.json"))
        cache = json.loads(store.read_text(report["cache"]))
        self.assertEqual(cache["v"], 1)
        self.assertEqual(cache["files"]["knowledge/k.md"]["since"], days_ago(90))
        self.assertIs(cache["files"]["knowledge/k.md"]["stale"], True)
        self.assertEqual(subprocess.run(status, capture_output=True, text=True).stdout, repo_before)

    def test_json_shape(self) -> None:
        self.write("knowledge/k.md", knowledge(days_ago(1), "x"))
        report = self.stale()
        self.assertEqual(
            set(report),
            {"days", "today", "entries", "stale", "budget_exhausted", "git_seconds", "cache"},
        )
        self.assertEqual(report["days"], 60)
        self.assertEqual(
            set(report["entries"][0]),
            {
                "file",
                "kind",
                "baseline",
                "baseline_source",
                "age_days",
                "aged",
                "drift",
                "missing_paths",
                "missing_repos",
                "unchecked",
                "sig",
                "stale",
            },
        )

    def test_bad_days_refused(self) -> None:
        code, _ = self.run_cli("stale", "--days", "0")
        self.assertEqual(code, 2)


class PackMarker(Base):
    PROFILE = "---\nrepo: hb\n---\n## Gotchas\n- see [[k]]\n"

    def setUp(self) -> None:
        super().setUp()
        self.write("projects/hb.md", self.PROFILE)
        self.write(
            "knowledge/k.md",
            knowledge(days_ago(90), "`app/x.php` [[hb]]", title="Old gotchas"),
        )
        self.retired()

    def pack(self) -> str:
        view = DiskVault(config.load(self.env))
        return packs.render_pack(view, Caps.from_config(self.cfg), "repo", "hb", [])

    def test_marker_shown_when_signature_matches(self) -> None:
        self.stale()
        text = self.pack()
        mark = f" (may be stale since {days_ago(90)})"
        self.assertIn("- Old gotchas · knowledge/k.md" + mark, text)
        self.assertNotIn("projects/hb.md (may be stale", text)
        self.assertLessEqual(len(text), self.cfg.get_int("caps", "pack_chars"))

    def test_marker_dropped_after_edit(self) -> None:
        self.stale()
        path = self.write(
            "knowledge/k.md",
            knowledge(days_ago(90), "`app/x.php` [[hb]] edited", title="Old gotchas"),
        )
        os.utime(path, ns=(1, 1))
        self.assertNotIn("may be stale", self.pack())

    def test_no_cache_no_marker(self) -> None:
        self.assertIn("Old gotchas", self.pack())
        self.assertNotIn("may be stale", self.pack())

    def test_profile_block_marked(self) -> None:
        self.write(
            "projects/hb.md",
            self.PROFILE.replace("repo: hb", f"repo: hb\nupdated: {days_ago(70)}"),
        )
        self.stale()
        self.assertIn(
            f"[Gotchas · projects/hb.md (may be stale since {days_ago(70)})]", self.pack()
        )

    def test_prompt_hook_ticket_pack_shows_marker(self) -> None:
        self.write("tasks/ABC-1.md", "---\ntitle: T\n---\n## Notes\nKeep it.\n")
        self.write("knowledge/k.md", knowledge(days_ago(90), "x", title="Old gotchas"))
        self.stale()
        out = self.hook(
            "user_prompt_submit", self.payload("UserPromptSubmit", prompt="look at ABC-1")
        )
        context = self.context(out)
        self.assertIn(
            f"- Old gotchas · knowledge/k.md (may be stale since {days_ago(90)})", context
        )
        self.assertLessEqual(len(json.dumps(out)), 9500)

    def forge(self, files: dict[str, Any]) -> DiskVault:
        os.makedirs(self.sb.state, exist_ok=True)
        with open(os.path.join(self.sb.state, "stale.json"), "w", encoding="utf-8") as handle:
            json.dump({"v": 1, "generated": int(time.time()), "files": files}, handle)
        return DiskVault(config.load(self.env))

    def test_forged_since_is_rejected(self) -> None:
        sig = store.file_sig(self.vp("knowledge/k.md"))
        for since in ("2026-01-01\n=== x", 5, None):
            entry = {"stale": True, "since": since, "sig": sig}
            self.assertEqual(self.forge({"knowledge/k.md": entry}).stale_mark("knowledge/k.md"), "")

    def test_bad_signature_is_rejected(self) -> None:
        entry: dict[str, Any] = {"stale": True, "since": "2026-01-01"}
        self.assertIsNone(
            self.forge({"knowledge/ghost.md": dict(entry, sig=None)}).stale_since(
                "knowledge/ghost.md"
            )
        )
        sig = store.file_sig(self.vp("knowledge/k.md"))
        assert sig is not None
        size, mtime = sig
        for bad in (
            [size],
            [size, str(mtime)],
            [True, mtime],
            [size, mtime, 0],
            "x",
            {},
            [float(size), mtime],
        ):
            view = self.forge({"knowledge/k.md": dict(entry, sig=bad)})
            self.assertEqual(view.stale_mark("knowledge/k.md"), "", bad)
        view = self.forge({"knowledge/k.md": dict(entry, sig=[size, mtime])})
        self.assertEqual(view.stale_mark("knowledge/k.md"), " (may be stale since 2026-01-01)")

    def test_bad_keys_are_rejected(self) -> None:
        entry: dict[str, Any] = {"stale": True, "since": "2026-01-01"}
        keys = (
            "knowledge/evil\n=== x.md",
            "rules/global.md",
            "knowledge/sub/x.md",
            "notes/n.md",
            "knowledge/a\u2028b.md",
            "projects/x.txt",
        )
        files = {}
        for key in keys:
            self.write(key, "x\n")
            files[key] = dict(entry, sig=store.file_sig(self.vp(key)))
        view = self.forge(files)
        for key in keys:
            self.assertIsNone(view.stale_since(key), key)


class Budget(Base):
    def setUp(self) -> None:
        super().setUp()
        self.commit("app/x.php", days_ago(1))
        self.write("knowledge/k.md", knowledge(days_ago(10), "`app/x.php`"))

    def test_budget_exhausted_marks_unchecked(self) -> None:
        with mock.patch.object(stale, "GIT_BUDGET_SECONDS", -1):
            report = stalescan.analyze(self.cfg, 60)
        entry = self.entry(report, "knowledge/k.md")
        self.assertTrue(report["budget_exhausted"])
        self.assertEqual(
            (entry["unchecked"], entry["drift"], entry["stale"]), (["app/x.php"], [], False)
        )
        self.assertIn("git budget of", stale.render(report))

    def test_git_timeout_marks_unchecked(self) -> None:
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 1.5)):
            report = stalescan.analyze(self.cfg, 60)
        entry = self.entry(report, "knowledge/k.md")
        self.assertFalse(report["budget_exhausted"])
        self.assertEqual((entry["unchecked"], entry["drift"]), (["app/x.php"], []))
        self.assertIn("  git not checked: app/x.php", stale.entry_lines(entry))

    def test_git_runs_with_the_inherited_git_env_stripped(self) -> None:
        seen: list[dict[str, str]] = []
        real = subprocess.run

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["env"])
            return real(*args, **kwargs)

        with (
            mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent"}),
            mock.patch("subprocess.run", side_effect=spy),
        ):
            report = stalescan.analyze(self.cfg, 60)
        self.assertTrue(seen)
        self.assertTrue(all("GIT_DIR" not in env for env in seen))
        self.assertEqual(self.entry(report, "knowledge/k.md")["drift"][0]["commits"], 1)


if __name__ == "__main__":
    unittest.main()
