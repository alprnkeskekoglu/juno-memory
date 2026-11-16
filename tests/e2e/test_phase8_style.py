import concurrent.futures
import json
import os
import re
import subprocess
import time
import unittest
from collections.abc import Callable
from typing import Any

from juno.cmd.init import STYLE_CHOICES
from tests.e2e import harness, judge
from tests.e2e.judge import JOKE_MARKS
from tests.support import Sandbox, e2e

PROMPT = "What is your name? Answer in one short sentence."
CLAIMS_JUNO = re.compile(r"(i'?m|i am|my name is|called|name's)\s+juno", re.IGNORECASE)


@e2e
class OutputStyleGivesTheIdentity(unittest.TestCase):
    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def ask(self, settings: dict[str, str]) -> str:
        extra = [
            "--setting-sources=local",
            "--settings",
            json.dumps(settings),
            "--no-session-persistence",
        ]
        run = harness.run_claude(
            PROMPT, harness.claude_env(self.sb, False), cwd=self.work, extra=extra
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        return run.result

    def test_selected_style_answers_juno(self) -> None:
        answer = self.ask({"outputStyle": STYLE_CHOICES["1"]})
        self.assertRegex(answer, CLAIMS_JUNO)

    def test_normal_style_answers_juno(self) -> None:
        answer = self.ask({"outputStyle": STYLE_CHOICES["2"]})
        self.assertRegex(answer, CLAIMS_JUNO)

    def test_without_the_style_it_does_not_claim_to_be_juno(self) -> None:
        answer = self.ask({})
        self.assertNotRegex(answer, CLAIMS_JUNO)

    def test_the_bare_plugin_name_selects_nothing(self) -> None:
        answer = self.ask({"outputStyle": "juno"})
        self.assertNotRegex(answer, CLAIMS_JUNO)


INCIDENT = (
    "prod is down: 5xx at 12% since the last deploy, customers are writing in. "
    "What do we do right now?"
)
DRAFT = "Draft a two-line message I can send to my team lead that the release slipped by a day."
ROUTINE = (
    "the feature flag for the new search was off again when I started testing, third time this "
    "week. turned it on now. unit tests first or e2e first?",
    "what's the git command to undo my last commit but keep the changes staged?",
    "give this a better name: `def do_stuff(d): return [x for x in d if x.active]`",
    "CI is green on my PR and I have one approval. anything left before I merge?",
    "I pushed a README typo fix straight to main instead of my branch. do I need to do anything?",
    "my teammate's PR has one 400-line function and no tests. how do I phrase my review?",
    "write a one-line commit message for: fixed the null check in the partner id lookup",
    "how do I list the 5 biggest files in a directory from the shell?",
    "I forgot to run the formatter before pushing again and the lint job failed. fastest fix?",
    "the vendor's SDK returns dates as strings in three different formats. how do I parse them "
    "safely in Python?",
)
FIRST = (
    "quick one: what's a good name for a boolean field that says whether a user's email address "
    "is verified?"
)
UNFINISHED = "QA of the CSV export is blocked until the staging database gets the new reports table"
CARD = (
    "# Handoffs \u00b7 alpha\n\n## {stamp} \u00b7 - \u00b7 csv export \u00b7 1a2b3c4d\nRepos: alpha\nTickets: -\n"
    "Built the CSV export for the reports page; unit tests pass.\n"
    "Stopped: {item}.\nNext: rerun QA of the CSV export once staging has the table.\n"
    "Links: -\nLearned: none\n"
)
PROFILE = "# alpha\n\n## Purpose\nReports service.\n"
PLAN = (
    "our checkout call to the payment service times out after 2 seconds about once an hour. I "
    "want to wrap it in a retry loop with 5 attempts and move on. give me the plan in a few "
    "lines, no code."
)
DECISION = (
    "noted, but I've decided: we do the 5-attempt retry. give me the plan for it in a few lines, "
    "no code."
)
DECIDED = "wrap the payment call in a retry loop with 5 attempts and get the plan for that"


SAMPLES = 5
ROUNDS = 2
WORKERS = 8
Job = tuple[str, str]


@e2e
class Case(unittest.TestCase):
    """Issue #35: sampled replies per dose, each judged once against the fixed rubric."""

    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def env(self) -> dict[str, str]:
        return harness.claude_env(self.sb, False)

    def run_reply(
        self,
        dose: str,
        prompt: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        resume: str = "",
        persist: bool = False,
    ) -> harness.Run:
        extra = [
            "--setting-sources=local",
            "--settings",
            json.dumps({"outputStyle": STYLE_CHOICES[dose]}),
        ]
        if resume:
            extra += ["--resume", resume]
        elif not persist:
            extra.append("--no-session-persistence")
        extra += ["--tools", ""]
        run = harness.run_claude(prompt, env or self.env(), cwd=cwd or self.work, extra=extra)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        return run

    def attempt(self, dose: str, prompt: str, **kw: Any) -> harness.Run:
        try:
            return self.run_reply(dose, prompt, **kw)
        except subprocess.TimeoutExpired:
            return self.run_reply(dose, prompt, **kw)

    def ask(self, dose: str, prompt: str) -> str:
        return self.attempt(dose, prompt).result

    def judged(
        self, criterion: str, dose: str, prompt: str, reply: str, **kw: Any
    ) -> judge.Verdict:
        verdict = judge.judge(reply, prompt, self.env(), self.work, **kw)
        judge.record(
            {"criterion": criterion, "dose": STYLE_CHOICES[dose], "prompt": prompt}
            | {"reply": reply, "verdict": verdict}
        )
        return verdict

    def sampled(
        self,
        criterion: str,
        jobs: list[Job],
        reply: Callable[[str, str], str] | None = None,
        workers: int = WORKERS,
        **kw: Any,
    ) -> list[tuple[str, judge.Verdict]]:
        """Reply to and judge every (dose, prompt) job, in job order."""
        make = reply or self.ask

        def one(job: Job) -> tuple[str, judge.Verdict]:
            text = make(*job)
            return text, self.judged(criterion, job[0], job[1], text, **kw)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, jobs))

    def check(self, result: tuple[bool, str], label: str) -> None:
        judge.record({"result": label, "pass": result[0], "why": result[1]})
        self.assertTrue(result[0], f"{label}: {result[1]}")


def per_dose(prompt: str) -> list[Job]:
    return [(dose, prompt) for dose in STYLE_CHOICES for _ in range(SAMPLES)]


def label(criterion: str, dose: str, index: int) -> str:
    return f"{criterion} {STYLE_CHOICES[dose]} #{index % SAMPLES + 1}"


@e2e
class CharacterHoldsItsRules(Case):
    """Criteria 1 and 3 of issue #35, judged, five replies per dose.

    The emoji regex covers the whole reply for criterion 1 and only the drafted message for
    criterion 3 (inside judge.criterion3): Juno's own lines around a draft may carry emoji.
    """

    def test_a_production_error_has_no_joke_or_emoji_at_any_dose(self) -> None:
        jobs = per_dose(INCIDENT)
        for i, ((dose, _), (reply, verdict)) in enumerate(
            zip(jobs, self.sampled("1", jobs), strict=True)
        ):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion1(reply, verdict), label("1", dose, i))
                self.assertNotRegex(reply, JOKE_MARKS)

    def test_a_drafted_outward_message_has_no_emoji_at_any_dose(self) -> None:
        jobs = per_dose(DRAFT)
        for i, ((dose, _), (_, verdict)) in enumerate(
            zip(jobs, self.sampled("3", jobs), strict=True)
        ):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion3(verdict), label("3", dose, i))


@e2e
class HumourDoseOnRoutineReplies(Case):
    """Criteria 2 and 6: ten routine replies per dose, two rounds; each round must pass."""

    def test_calm_jokes_at_most_once_playful_more_than_normal_no_third_party_teasing(
        self,
    ) -> None:
        jobs = [(dose, prompt) for dose in STYLE_CHOICES for prompt in ROUTINE]
        found = [v for _, v in self.sampled("2,6", jobs * ROUNDS)]
        rounds = [found[r * len(jobs) : (r + 1) * len(jobs)] for r in range(ROUNDS)]
        for number, verdicts in enumerate(rounds, 1):
            by_dose = {
                dose: [v for (d, _), v in zip(jobs, verdicts, strict=True) if d == dose]
                for dose in STYLE_CHOICES
            }
            with self.subTest(criterion=2, round=number):
                self.check(judge.criterion2(*by_dose.values()), f"2 round {number}")
        for dose in STYLE_CHOICES:
            found = [v for vs in rounds for (d, _), v in zip(jobs, vs, strict=True) if d == dose]
            with self.subTest(criterion=6, dose=dose):
                self.check(judge.criterion6(found), f"6 {STYLE_CHOICES[dose]}")


@e2e
class FirstReplyCase(Case):
    """A real SessionStart injection in a repo with a seeded profile; the first reply is judged.

    Every sample gets its own sandbox (vault, state, config, repo root, projects dir), so the
    samples run concurrently and no sample sees another one's state.
    """

    def setUp(self) -> None:
        super().setUp()
        self.card = False

    def seed_card(self) -> None:
        self.card = True

    def sample_box(self) -> tuple[Sandbox, str]:
        sb = Sandbox()
        done = harness.init_with_bin(sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[handoff]\nmode = "off"\n')
        repo = os.path.join(sb.root, "projects", "alpha")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
        files = {"projects/alpha.md": PROFILE}
        if self.card:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 86400))
            files["handoffs/alpha.md"] = CARD.format(stamp=stamp, item=UNFINISHED)
        for rel, text in files.items():
            with open(os.path.join(sb.vault, *rel.split("/")), "w", encoding="utf-8") as handle:
                handle.write(text)
        return sb, repo

    def first_reply(self, dose: str, prompt: str) -> tuple[str, str]:
        sb, repo = self.sample_box()
        try:
            env = harness.claude_env(sb, True)
            env["JUNO_REPO_ROOTS"] = os.path.dirname(repo)
            env["JUNO_CLAUDE_PROJECTS"] = os.path.join(sb.root, "claude-projects")
            run = self.attempt(dose, prompt, env=env, cwd=repo)
        finally:
            sb.cleanup()
        self.assertEqual(harness.juno_hook_failures(run), [])
        contexts = harness.juno_contexts(run, "SessionStart")
        self.assertEqual(len(contexts), 1, run.raw[:2000])
        return run.result, contexts[0]

    def with_card(self, dose: str, prompt: str) -> str:
        reply, context = self.first_reply(dose, prompt)
        self.assertIn(UNFINISHED, context)
        return reply

    def without_card(self, dose: str, prompt: str) -> str:
        reply, context = self.first_reply(dose, prompt)
        self.assertNotIn("Card · ", context)
        return reply


@e2e
class SessionStartGreetsWithOneUnfinishedItem(FirstReplyCase):
    """Criterion 4: the first reply names one injected unfinished item and invents none."""

    def test_an_injected_unfinished_item_is_named_once(self) -> None:
        self.seed_card()
        jobs = per_dose(FIRST)
        found = self.sampled("4", jobs, self.with_card, items=[UNFINISHED])
        for i, ((dose, _), (_, verdict)) in enumerate(zip(jobs, found, strict=True)):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion4(verdict, True), label("4 with", dose, i))

    def test_no_unfinished_item_is_invented_when_none_is_injected(self) -> None:
        jobs = per_dose(FIRST)
        found = self.sampled("4", jobs, self.without_card, items=[])
        for i, ((dose, _), (_, verdict)) in enumerate(zip(jobs, found, strict=True)):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion4(verdict, False), label("4 none", dose, i))


@e2e
class SeriousFirstMessageSkipsTheGreeting(FirstReplyCase):
    """Criterion 11: a session that opens on a production error calms first and greets nobody."""

    def test_a_production_error_as_the_first_message_calms_first_without_a_greeting(self) -> None:
        self.seed_card()
        jobs = per_dose(INCIDENT)
        found = self.sampled("11", jobs, self.with_card, items=[UNFINISHED])
        for i, ((dose, _), (_, verdict)) in enumerate(zip(jobs, found, strict=True)):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion11(verdict), label("11", dose, i))


@e2e
class DisagreeThenFollowTheDecision(Case):
    """Criterion 5: an objection with reason and alternative, then the next reply follows."""

    def exchange(self, job: Job) -> tuple[judge.Verdict, judge.Verdict]:
        dose = job[0]
        first = self.attempt(dose, PLAN, persist=True)
        sid = next(str(e["session_id"]) for e in first.events if e.get("session_id"))
        objected = self.judged("5", dose, PLAN, first.result)
        second = self.attempt(dose, DECISION, resume=sid)
        return objected, self.judged("5", dose, DECISION, second.result, decision=DECIDED)

    def test_objects_then_does_what_the_user_decided(self) -> None:
        jobs = per_dose(PLAN)
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            found = list(pool.map(self.exchange, jobs))
        for i, ((dose, _), (objected, followed)) in enumerate(zip(jobs, found, strict=True)):
            with self.subTest(dose=dose, sample=i % SAMPLES):
                self.check(judge.criterion5_objection(objected), label("5 objection", dose, i))
                self.check(judge.criterion5_follow(followed), label("5 follow", dose, i))


PLANTED_JOKE = (
    "calm, we're in control. the last deploy clearly wanted prod to have a more exciting day than "
    "we did. 5xx at 12% since that deploy, so a rollback fixes it right away: roll back now, then "
    "we read the diff together."
)
PLANTED_CLEAN = (
    "calm, we're in control. 5xx at 12% on prod since the last deploy. a rollback fixes it right "
    "away: roll back now, then we read the diff together."
)


@e2e
class JudgeCatchesAJokeAboutAProductionError(Case):
    """Criterion 10: a planted verbal joke about a production error fails criterion 1."""

    def test_a_planted_joke_in_words_fails_criterion_1(self) -> None:
        self.assertNotRegex(PLANTED_JOKE, JOKE_MARKS)
        verdict = judge.judge(PLANTED_JOKE, INCIDENT, self.env(), self.work)
        judge.record({"criterion": "10", "reply": PLANTED_JOKE, "verdict": verdict})
        ok, why = judge.criterion1(PLANTED_JOKE, verdict)
        judge.record({"result": "10 planted joke", "pass": not ok, "why": why})
        self.assertTrue(verdict["joke_in_words"], verdict)
        self.assertFalse(ok, verdict)

    def test_the_same_reply_without_the_joke_passes_criterion_1(self) -> None:
        verdict = judge.judge(PLANTED_CLEAN, INCIDENT, self.env(), self.work)
        judge.record({"criterion": "10 control", "reply": PLANTED_CLEAN, "verdict": verdict})
        result = judge.criterion1(PLANTED_CLEAN, verdict)
        judge.record({"result": "10 clean control", "pass": result[0], "why": result[1]})
        self.assertTrue(result[0], verdict)


if __name__ == "__main__":
    unittest.main()
