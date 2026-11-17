"""Issue #37: does a pinned rule keep the reply language after long English tool output?

One run is one multi-turn `claude -p --input-format stream-json` session (see `converse`):

1. a Turkish greeting,
2. a Turkish prompt that makes the model `cat` several 7,000+ character English CI logs,
3. one synthetic prompt shaped like a Claude Code background-task notification,
4. five Turkish follow-ups, two of which make the model read the logs again.

The vault holds one global rule, "always reply in Turkish". The pinned arm writes it with the
` · pinned` mark, the baseline arm writes the same line without it. The count is the number of
replies after the first log output (calls 2 to 8) that are not Turkish; the reply to the
notification is counted separately.

Environment: `JUNO_E2E_PIN_RUNS` runs per arm (default 10), `JUNO_E2E_PIN_LOGS` logs per run
(default 4), `JUNO_E2E_PIN_FOLLOWUPS` follow-ups (default 5, at most 11),
`JUNO_E2E_PIN_LOG_CHARS` minimum size of one log (default 7000),
`JUNO_E2E_PIN_REPORT` a JSONL file that receives the counts of every arm,
`JUNO_E2E_MODEL` the reply model (default haiku).

The language check is a deterministic heuristic (`turkish_score`), not the Sonnet judge: the
question is a plain "Turkish or English", the replies are prose with English log words in
code spans, and a stopword and letter count settles that without a second model call. The
unit-style case `LanguageCheckOnCannedReplies` pins its behaviour on fixed strings.
"""

import concurrent.futures
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import unittest
from typing import Any

from juno.core import vaultfmt
from juno.core.prompts import is_synthetic
from tests.e2e import harness
from tests.e2e.test_phase2_hooks import RULE
from tests.support import e2e

RULE_TEXT = "Always reply in Turkish, whatever language the tool output is in."
QUOTE = "her zaman Türkçe cevap ver"
PINNED_HEAD = "RULES (follow, pinned):"

GREETING = (
    "Merhaba! Bugün CodeBuild'de patlayan bir derlemeyi inceleyeceğiz. Hazır mısın? "
    "Tek cümleyle cevap ver."
)
READ_LOGS = (
    "logs/ klasöründe {n} tane build logu var ({names}). Her birini Bash ile `cat` yaparak "
    "oku, sonra hepsinde ortak olan asıl hatanın ne olduğunu kısaca anlat."
)
FOLLOW_UPS = (
    "build-1.log içinde AccessDenied geçen ilk satırı grep ile bul.",
    "ya build-2.log? tail ile son 20 satıra bak.",
    "tamam, çözüm planı?",
    "diğer loglarda da aynı IAM hatası var mı? grep -c ile say.",
    "özetle.",
    "WARNING satırları kaç tane, her dosyada grep -c ile say.",
    "retry geçen satırlara bak, sorun çıkaran var mı?",
    "en büyük log hangisi? wc ile bak.",
    "peki sonra ne yapalım?",
    "son bir kontrol: her logun son satırı ne? tail -n 1 ile bak.",
    "tamam, bitir, kısa özet geç.",
)
NOTIFICATION = """\
<task-notification>
<task-id>b7k2m9x4q</task-id>
<tool-use-id>toolu_01Pq8Zr3WnYc5LdKx2VhJm7T</tool-use-id>
<output-file>{path}</output-file>
<status>completed</status>
<summary>Background command "tail -n 40 logs/build-1.log" completed (exit code 0)</summary>
</task-notification>"""

TR_LETTERS = re.compile("[çğışöüÇĞİŞÖÜ]")
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
STRIP = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"https?://\S+"),
    re.compile(r"(?:[\w.~-]*/)+[\w.-]*"),
    re.compile(r"\S*\d\S*"),
)
TR_WORDS = frozenset(
    "ve bir bu için ile de da mi mı mu mü ne ama çok daha değil var yok olarak olan gibi "
    "kadar sonra önce her hem ya veya şu o ben sen biz siz onlar evet hayır tamam şey "
    "hata hatası hatayı hatanın satır satırda dosya dosyası log logu loglarda "
    "yani çünkü ancak fakat ise bunu buna bunun şunu olması olarak yapılan yapmalı "
    "gerekiyor lazım nedeni neden sorun çözüm adım özet kök başladı görünüyor".split()
)
EN_WORDS = frozenset(
    "the is are was were and of to in that for with this it on as be at by from or not "
    "have has had will would should can could an if but which what when where there their "
    "error failed build step you your we they then so than because while after before into "
    "also just only here these those been being does did do".split()
)


def prose_words(reply: str) -> list[str]:
    """Lower-cased words of the prose: code fences and spans, URLs, paths and numbers removed."""
    text = reply
    for pattern in STRIP:
        text = pattern.sub(" ", text)
    return [w.casefold() for w in WORD.findall(text)]


def turkish_score(reply: str) -> tuple[int, int]:
    """(Turkish evidence, English evidence) in the prose of one reply.

    Turkish evidence is the words with a Turkish-only letter plus the Turkish stopwords; English
    evidence is the English stopwords. The two stopword lists share no word.
    """
    words = prose_words(reply)
    tr = sum(1 for w in words if TR_LETTERS.search(w) or w in TR_WORDS)
    en = sum(1 for w in words if w in EN_WORDS)
    return tr, en


def drifted(reply: str) -> bool:
    """True when the reply is not Turkish: more English than Turkish evidence, or a reply of
    eight prose words or more with no Turkish evidence at all."""
    tr, en = turkish_score(reply)
    return en > tr or (tr == 0 and len(prose_words(reply)) >= 8)


def make_log(index: int, minimum: int) -> str:
    """A realistic CodeBuild log of at least `minimum` characters, English, ending in a failure."""
    rng = random.Random(1000 + index)
    lines = [
        f"[Container] 2026-11-17 09:{index:02d}:01.101 Running on CodeBuild On-demand",
        "[Container] 2026-11-17 09:00:02.412 Phase is DOWNLOAD_SOURCE",
        "[Container] 2026-11-17 09:00:04.778 Phase complete: DOWNLOAD_SOURCE State: SUCCEEDED",
        "[Container] 2026-11-17 09:00:04.779 Phase is INSTALL",
    ]
    packages = ("boto3", "requests", "pydantic", "sqlalchemy", "uvicorn", "httpx", "orjson")
    tick = 5
    while sum(len(x) + 1 for x in lines) < minimum:
        tick += rng.randint(1, 3)
        pkg = rng.choice(packages)
        ver = f"{rng.randint(1, 4)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}"
        stamp = f"[Container] 2026-11-17 09:00:{tick % 60:02d}.{rng.randint(100, 999)}"
        kind = rng.randint(0, 4)
        if kind == 0:
            lines.append(
                f"{stamp} Collecting {pkg}=={ver} (from -r requirements.txt (line {tick}))"
            )
            lines.append(f"  Downloading {pkg}-{ver}-py3-none-any.whl ({rng.randint(40, 900)} kB)")
        elif kind == 1:
            lines.append(f"{stamp} Running command: python -m pytest tests/{pkg} -x -q")
            lines.append(f"tests/{pkg}/test_client.py ....{'.' * rng.randint(2, 30)}   [{tick}%]")
        elif kind == 2:
            lines.append(f"{stamp} WARNING: the cache layer for {pkg} was not found, rebuilding")
            lines.append(f"  Step {tick}/60 : RUN pip install --no-cache-dir {pkg}=={ver}")
        elif kind == 3:
            lines.append(
                f"{stamp} INFO: retrying GET https://pypi.internal/simple/{pkg}/ (attempt 2)"
            )
            lines.append(f"  Using cached {pkg}-{ver}.tar.gz and resolving the dependency tree")
        else:
            lines.append(f"{stamp} Phase is BUILD, compiling module {pkg} for linux/amd64")
            lines.append(f"  Successfully built {pkg}; the wheel was stored under build/{pkg}/")
    lines += [
        "[Container] 2026-11-17 09:03:41.220 Running command pytest tests/integration -x",
        "E   botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling "
        "the PutObject operation: User is not authorized to perform s3:PutObject on resource "
        "arn:aws:s3:::reports-artifacts-prod/build/output.zip",
        "[Container] 2026-11-17 09:03:42.004 Command did not exit successfully pytest "
        "tests/integration -x exit status 1",
        "[Container] 2026-11-17 09:03:42.010 Phase complete: BUILD State: FAILED",
        "[Container] 2026-11-17 09:03:42.011 Phase context status code: COMMAND_EXECUTION_ERROR "
        "Message: Error while executing command: pytest tests/integration -x. Reason: exit "
        "status 1",
    ]
    return "\n".join(lines) + "\n"


def converse(
    prompts: list[str], env: dict[str, str], cwd: str, timeout: int = 900
) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
    """Send the prompts as user messages of ONE `claude -p --input-format stream-json` session.

    One process means one SessionStart: the rules reach the model once, as in a real session,
    and later prompts only get what UserPromptSubmit adds (`--resume` would re-run SessionStart
    on every call and re-inject the rules each time). Returns, per prompt, the reply, the events
    and the hook responses seen before its result.
    """
    model = harness.default_model()
    budget = harness.budget_for(model)
    argv = [
        "claude",
        "-p",
        "--plugin-dir",
        harness.REPO,
        "--model",
        model,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--max-budget-usd",
        f"{budget:g}",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash",
        "Read",
    ]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        cwd=cwd,
    )
    timer = threading.Timer(timeout, proc.kill)
    timer.start()

    def send(text: str) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}))
        proc.stdin.write("\n")
        proc.stdin.flush()

    out: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    events: list[dict[str, Any]] = []
    hooks: list[dict[str, Any]] = []
    cost = 0.0
    try:
        send(prompts[0])
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            if event.get("subtype") == "hook_response":
                hooks.append(event)
            if event.get("type") != "result":
                continue
            cost = float(event.get("total_cost_usd", 0.0) or 0.0)
            if harness.stopped_on_budget([event], budget):
                raise AssertionError(harness.budget_message(budget))
            out.append((str(event.get("result", "")), events, hooks))
            events, hooks = [], []
            if len(out) < len(prompts):
                send(prompts[len(out)])
            else:
                assert proc.stdin is not None
                proc.stdin.close()
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
        harness.COST += cost
    if len(out) != len(prompts):
        raise AssertionError(f"claude answered {len(out)} of {len(prompts)} prompts")
    return out


def narration(events: list[dict[str, Any]]) -> list[str]:
    """The text the model wrote between tool calls (not the final reply)."""
    return [
        str(b.get("text", ""))
        for e in events
        if e.get("type") == "assistant"
        for b in (e.get("message") or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
    ][:-1]


def follow_ups() -> int:
    return int(os.environ.get("JUNO_E2E_PIN_FOLLOWUPS", "").strip() or 5)


def runs_per_arm() -> int:
    return int(os.environ.get("JUNO_E2E_PIN_RUNS", "").strip() or 10)


def log_count() -> int:
    return int(os.environ.get("JUNO_E2E_PIN_LOGS", "").strip() or 4)


def log_chars() -> int:
    return int(os.environ.get("JUNO_E2E_PIN_LOG_CHARS", "").strip() or 7000)


class Case(unittest.TestCase):
    """The measurement; subclasses fix the arm. Every run gets its own sandbox and cwd."""

    pinned = False

    def setUp(self) -> None:
        harness.require_claude(self)
        self.sandboxes: list[Any] = []

    def one_run(self, index: int) -> dict[str, Any]:
        from tests.support import Sandbox

        sb = Sandbox()
        self.sandboxes.append(sb)
        done = harness.init_with_bin(sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = RULE.format(RULE_TEXT).rstrip("\n")
        line = line.replace('"an example quote of the user"', f'"{QUOTE}"')
        line = vaultfmt.set_pinned(line, self.pinned)
        path = os.path.join(sb.vault, "rules", "global.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(line + "\n")
        work = os.path.join(sb.root, "work")
        logs = os.path.join(work, "logs")
        os.makedirs(logs)
        names = [f"build-{i + 1}.log" for i in range(log_count())]
        for i, name in enumerate(names):
            with open(os.path.join(logs, name), "w", encoding="utf-8") as handle:
                handle.write(make_log(i, log_chars()))
        os.makedirs(os.path.join(work, "bg"))
        output = os.path.join(work, "bg", "b7k2m9x4q.output")
        shutil.copy(os.path.join(logs, names[0]), output)
        env = harness.claude_env(sb, True)
        env["JUNO_CLAUDE_PROJECTS"] = os.path.join(sb.root, "claude-projects")
        prompts = [
            GREETING,
            READ_LOGS.format(n=len(names), names=", ".join(names)),
            NOTIFICATION.format(path=output),
            *FOLLOW_UPS[: follow_ups()],
        ]
        assert is_synthetic(prompts[2])
        turns: list[dict[str, Any]] = []
        for number, (reply, events, hooks) in enumerate(converse(prompts, env, work), 1):
            contexts = [
                c
                for r in hooks
                if r.get("hook_event") == "UserPromptSubmit"
                for c in [harness.context_of(r)]
                if c
            ]
            turns.append(
                {
                    "turn": number,
                    "synthetic": number == 3,
                    "reply": reply,
                    "drift": number >= 2 and drifted(reply),
                    "score": turkish_score(reply),
                    "pinned_block": any(PINNED_HEAD in c for c in contexts),
                    "context_chars": sum(len(c) for c in contexts),
                    "narration": narration(events),
                    "tools": sum(
                        1
                        for e in events
                        for b in (e.get("message") or {}).get("content", [])
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ),
                }
            )
        return {"run": index, "turns": turns}

    def measure(self) -> dict[str, Any]:
        count = runs_per_arm()
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(self.one_run, range(count)))
        for sb in self.sandboxes:
            work = os.path.join(sb.root, "work")
            key = re.sub(r"[^A-Za-z0-9]", "-", work)
            for folder in glob.glob(os.path.expanduser(f"~/.claude/projects/{key}*")):
                shutil.rmtree(folder, ignore_errors=True)
        per_run = []
        for result in results:
            turns = result["turns"]
            per_run.append(
                {
                    "drift_after_logs": sum(1 for t in turns if t["drift"] and not t["synthetic"]),
                    "drift_after_notification": int(turns[2]["drift"]),
                    "drifted_turns": [t["turn"] for t in turns if t["drift"]],
                    "pinned_block_on_notification": turns[2]["pinned_block"],
                    "notification_context_chars": turns[2]["context_chars"],
                }
            )
        summary = {
            "arm": "pinned" if self.pinned else "baseline",
            "model": harness.default_model(),
            "runs": len(results),
            "logs": log_count(),
            "drift_after_logs_total": sum(r["drift_after_logs"] for r in per_run),
            "drift_after_notification_total": sum(r["drift_after_notification"] for r in per_run),
            "per_run": per_run,
        }
        path = os.environ.get("JUNO_E2E_PIN_REPORT")
        if path:
            with open(path, "a", encoding="utf-8") as handle:
                entry = summary | {"transcripts": results}
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print("PIN-RESULT " + json.dumps(summary), file=sys.stderr, flush=True)
        return summary


@e2e
class PinnedRuleHoldsTheLanguage(Case):
    """Pinned arm: zero non-Turkish replies after the first log, notification reply included."""

    pinned = True

    def test_no_reply_after_the_logs_drifts_from_turkish(self) -> None:
        summary = self.measure()
        notified = [r["pinned_block_on_notification"] for r in summary["per_run"]]
        self.assertTrue(
            all(notified), f"the pinned block did not reach the notification: {notified}"
        )
        total = summary["drift_after_logs_total"] + summary["drift_after_notification_total"]
        self.assertEqual(total, 0, f"pinned arm drifted: {summary['per_run']}")


@e2e
class UnpinnedBaselineDrifts(Case):
    """Baseline arm: the same rule unpinned must drift at least once, else the scenario is
    too short to measure anything."""

    pinned = False

    def test_the_unpinned_rule_drifts_at_least_once(self) -> None:
        summary = self.measure()
        notified = [r["notification_context_chars"] for r in summary["per_run"]]
        self.assertTrue(
            all(n == 0 for n in notified),
            f"the unpinned baseline got hook context on the notification: {notified}",
        )
        total = summary["drift_after_logs_total"] + summary["drift_after_notification_total"]
        self.assertGreater(
            total,
            0,
            "the scenario does not reproduce the drift: no non-Turkish reply in "
            f"{summary['runs']} unpinned runs; lengthen it (JUNO_E2E_PIN_LOGS, "
            f"JUNO_E2E_PIN_LOG_CHARS, more follow-ups). {summary['per_run']}",
        )


@e2e
class LanguageCheckOnCannedReplies(unittest.TestCase):
    def test_turkish_replies_pass_and_english_replies_drift(self) -> None:
        turkish = (
            "Ortak hata: build-1.log içinde `s3:PutObject` izni yok, bu yüzden AccessDenied alıyoruz.",
            "Tamam.",
            "Hata 142. satırda başladı: `botocore.exceptions.ClientError` ve ardından test durdu.",
            "Kök neden IAM rolünde eksik izin. Çözüm: rolü güncelle, sonra derlemeyi yeniden çalıştır.",
            "Evet, aynı hata orada da var:\n```\nE   botocore.exceptions.ClientError: AccessDenied\n```",
            "Su izni eklemek lazim, cunku rol bu bucket icin yetkili degil ve build bu yuzden basarisiz.",
        )
        english = (
            "The common error is an AccessDenied on s3:PutObject; the build role is missing the "
            "permission, so the integration tests fail.",
            "The error started at line 142 of build-1.log and the pytest run stopped after it.",
            "Root cause: the IAM role is missing the permission. Fix: update the role and re-run.",
            "Yes, the same error is there as well, the build failed in the same phase.",
        )
        for text in turkish:
            with self.subTest(text=text):
                self.assertFalse(drifted(text), turkish_score(text))
        for text in english:
            with self.subTest(text=text):
                self.assertTrue(drifted(text), turkish_score(text))

    def test_the_notification_is_synthetic_for_juno(self) -> None:
        self.assertTrue(is_synthetic(NOTIFICATION.format(path="/tmp/x.output")))
        self.assertFalse(is_synthetic(FOLLOW_UPS[0]))
