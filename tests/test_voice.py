import json
import os
import unittest
from typing import Any

from juno.core import voicecollect, voicecues, voicemeasure
from tests.support import REPO
from tests.support.banned import local_banned
from tests.support.hooks import SID, HookCase

TOOL = "mcp__chat__send_message"
DRAFT = (
    "Hello Anna, thank you very much for the detailed review of the migration. I checked every "
    "point and the rollout plan looks fine to me, so we can merge it today."
)


class Core(unittest.TestCase):
    def test_outward_patterns_cover_mcp_tools_and_gh_commands(self) -> None:
        from juno import config

        patterns = config.DEFAULTS["voice"]["outward_tools"]
        hit = voicecollect.matches_outward
        self.assertTrue(hit("mcp__x__send_message", {}, patterns))
        self.assertTrue(hit("mcp__x__add_comment_to_issue", {}, patterns))
        self.assertTrue(hit("Bash", {"command": "gh pr comment 4 --body 'ok'"}, patterns))
        self.assertTrue(hit("Bash", {"command": "gh pr review 4 --approve --body 'ok'"}, patterns))
        self.assertTrue(hit("Bash", {"command": "gh issue comment 4 -b ok"}, patterns))
        self.assertFalse(hit("Bash", {"command": "git status"}, patterns))
        self.assertFalse(hit("mcp__x__list_messages", {}, patterns))

    def test_post_text_from_tool_input_and_gh_body(self) -> None:
        self.assertEqual(voicecollect.post_text("mcp__a__b", {"body": " hi there "}), "hi there")
        cmd = "gh pr comment 3 --body 'looks fine; merging'"
        self.assertEqual(voicecollect.post_text("Bash", {"command": cmd}), "looks fine; merging")
        heredoc = "gh issue comment 3 --body \"$(cat <<'EOF'\nline one\nline two\nEOF\n)\""
        self.assertEqual(voicecollect.post_text("Bash", {"command": heredoc}), "line one\nline two")

    def test_measures_come_only_from_the_given_samples(self) -> None:
        texts = ["hi", "ok then", "fine.", "yes!", "no way"]
        m = voicemeasure.measure(texts)
        self.assertEqual((m.n, m.med_chars, m.p90_chars), (5, 5, 7))
        self.assertAlmostEqual(m.lower_start, 1.0)
        self.assertAlmostEqual(m.period_end, 0.2)
        self.assertEqual(m.emoji, 0.0)

    def test_example_filter_skips_names_ids_and_emails(self) -> None:
        keep = voicemeasure.clean_example
        self.assertTrue(keep("ok, merged it", None))
        self.assertFalse(keep("thanks Alice, merged", None))
        self.assertFalse(keep("mail me at someone@example.com", None))
        self.assertFalse(keep("ping @carol", None))

    def test_cue_lists_name_no_company_person_or_plugin(self) -> None:
        text = json.dumps(voicecues.PRESETS, ensure_ascii=False).casefold()
        sources = ""
        for name in ("voicecollect", "voicemeasure", "voicecues"):
            with open(os.path.join(REPO, "juno", "core", f"{name}.py"), encoding="utf-8") as h:
                sources += h.read().casefold()
        with open(os.path.join(REPO, "juno", "adapters", "voiceio.py"), encoding="utf-8") as h:
            sources += h.read().casefold()
        for word in local_banned():
            self.assertNotIn(word, text + sources)
        for word in ("slack", "jira", "atlassian", "linear"):
            self.assertNotIn(word, text + sources)


class VoiceCase(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_config('[handoff]\nmode = "reminder"\n')

    def seed(self, texts: list[str], reg: str = "voice", by: str = "user") -> None:
        rows = [
            {
                "k": "sample",
                "id": f"{reg}{i}",
                "t": 1.0 + i,
                "sid": "seed",
                "reg": reg,
                "kind": "chat",
                "by": by,
                "text": text,
            }
            for i, text in enumerate(texts)
        ]
        path = os.path.join(self.sb.state, "voice.jsonl")
        os.makedirs(self.sb.state, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    def prompt(self, text: str, **extra: Any) -> str:
        out = self.hook(
            "user_prompt_submit", self.payload("user_prompt_submit", prompt=text, **extra)
        )
        return self.context(out)

    def stop(self, message: str) -> None:
        self.hook("stop", self.payload("stop", last_assistant_message=message))

    def post(self, text: str, tool: str = TOOL, **extra: Any) -> None:
        self.hook(
            "post_tool_use",
            self.payload(
                "post_tool_use",
                tool_name=tool,
                tool_input={"text": text},
                tool_response={},
                tool_use_id="t1",
                **extra,
            ),
        )

    def events(self, name: str) -> list[dict[str, Any]]:
        return [r for r in self.ledger() if r.get("ev") == name]

    def rows(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "voice.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]


class Collect(VoiceCase):
    def test_three_posts_are_marked_user_edited_and_claude(self) -> None:
        self.prompt("post this message: ok merged, thanks for the fix")
        self.post("ok merged, thanks for the fix")
        self.prompt("write a reply to the thread")
        self.stop(DRAFT)
        self.prompt("too long, shorten it")
        self.post("migration rollout looks fine, merging today")
        self.stop("ok, will merge after lunch")
        self.prompt("ok post it")
        self.post("ok, will merge after lunch")
        samples = self.events("voice")
        self.assertEqual([s["by"] for s in samples], ["user", "edited", "claude"])
        self.assertEqual(samples[1]["draft"], DRAFT)
        self.assertEqual({s["kind"] for s in samples}, {"chat"})

    def test_correction_is_stored_with_its_draft_and_shows_next_time(self) -> None:
        self.seed([f"short note {i}" for i in range(15)])
        self.prompt("write a reply to him")
        self.stop(DRAFT)
        self.prompt("too long, sounds like AI")
        fixes = self.events("voice_fix")
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0]["quote"], "too long, sounds like AI")
        self.assertEqual(fixes[0]["draft"], DRAFT)
        context = self.prompt("write a message to him again")
        self.assertIn('"too long, sounds like AI"', context)

    def test_nosave_private_subagent_and_headless_collect_nothing(self) -> None:
        self.prompt("write a reply [nosave]")
        self.post("quiet text")
        self.assertEqual(self.events("voice"), [])
        self.assertEqual(self.events("voice_fix"), [])

    def test_private_segment_collects_nothing(self) -> None:
        self.prompt("hello there")
        self.hook("post_tool_use", self.payload("post_tool_use", tool_name="Bash", tool_input={}))
        with open(
            os.path.join(self.sb.state, "ledgers", SID + ".jsonl"), "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps({"ev": "private", "t": 5.0}) + "\n")
        self.post("secret text")
        self.assertEqual(self.events("voice"), [])

    def test_subagent_posts_are_not_collected(self) -> None:
        self.prompt("hello there")
        self.post("from a subagent", agent_id="a1")
        self.assertEqual(self.events("voice"), [])

    def test_headless_run_writes_nothing(self) -> None:
        env = {"JUNO_TEST_INTERACTIVE": "0"}
        self.hook(
            "user_prompt_submit",
            self.payload("user_prompt_submit", prompt="write a reply"),
            env=env,
        )
        self.hook(
            "post_tool_use",
            self.payload(
                "post_tool_use", tool_name=TOOL, tool_input={"text": "x y z"}, tool_response={}
            ),
            env=env,
        )
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.rows(), [])

    def test_quiet_segment_removes_its_ingested_rows_at_session_end(self) -> None:
        self.seed(["a b"], by="user")
        self.prompt("post: fine with me [nosave]")
        self.post("fine with me")
        self.hook("session_end", self.payload("session_end", reason="other"))
        self.assertEqual(len(self.rows()), 1)


class Profile(VoiceCase):
    def test_fifteen_user_samples_write_self_voice_md(self) -> None:
        clean = [f"ok merged it {chr(97 + i)}" for i in range(13)]
        dirty = ["thanks Alice, merged", "mail me at someone@example.com"]
        for text in [*clean, *dirty]:
            self.prompt(f"post this message: {text}")
            self.post(text)
        self.hook("session_end", self.payload("session_end", reason="other"))
        with open(os.path.join(self.sb.vault, "self", "voice.md"), encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("visibility: private", body)
        self.assertIn("## voice (15 samples)", body)
        self.assertIn("length: median", body)
        examples = body.split("Examples (verbatim, the user's own):")[1].strip().splitlines()
        shown = [line for line in examples if line.startswith("- ")]
        self.assertEqual(len(shown), 8)
        self.assertNotIn("Alice", body)
        self.assertNotIn("someone@example.com", body)
        self.assertIn("## formal (0 samples)", body)

    def test_below_the_threshold_no_file_is_written(self) -> None:
        for i in range(3):
            self.prompt(f"post this message: fine number {i}")
            self.post(f"fine number {i}")
        self.hook("session_end", self.payload("session_end", reason="other"))
        self.assertFalse(os.path.exists(os.path.join(self.sb.vault, "self", "voice.md")))
        self.assertEqual(len(self.rows()), 3)

    def test_second_session_end_does_not_duplicate_rows(self) -> None:
        self.prompt("post this message: fine with me")
        self.post("fine with me")
        for _ in range(2):
            self.hook("session_end", self.payload("session_end", reason="other"))
        self.assertEqual(len(self.rows()), 1)


class Inject(VoiceCase):
    def seed_both(self) -> None:
        self.seed([f"yo merged {i}" for i in range(15)], "voice")
        self.seed([f"Thank you, the change {i} is merged." for i in range(15)], "formal")

    def test_draft_prompt_gets_the_voice_profile(self) -> None:
        self.seed_both()
        context = self.prompt("write a message to him about the merge")
        self.assertIn("Register: voice", context)
        self.assertIn("yo merged", context)
        self.assertNotIn("Thank you, the change", context)
        self.assertIn("Two voices never mix", context)
        self.assertLessEqual(len(context), 9500)

    def test_formal_cue_gets_the_formal_profile(self) -> None:
        self.seed_both()
        context = self.prompt("write a message to him about the merge, formal")
        self.assertIn("Register: formal", context)
        self.assertIn("Thank you, the change", context)
        self.assertNotIn("yo merged", context)

    def test_plain_prompt_gets_no_voice_block(self) -> None:
        self.seed_both()
        self.assertNotIn("Juno voice", self.prompt("what does this function do"))

    def test_below_the_threshold_only_the_base_list(self) -> None:
        self.seed(["yo merged 1", "yo merged 2", "yo merged 3"])
        context = self.prompt("write a message to him")
        self.assertIn("Avoid the AI tells", context)
        self.assertIn("3 of 15 samples", context)
        self.assertNotIn("median", context)
        self.assertNotIn("yo merged", context)

    def test_turkish_preset_cues(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.assertIn("Juno voice", self.prompt("ona bir mesaj yaz"))


class Check(VoiceCase):
    def test_long_bulleted_emoji_draft_gets_a_context_line_and_is_not_blocked(self) -> None:
        self.seed(["x" * 20 for _ in range(15)])
        self.prompt("hello there")
        body = "- aaa\n- bbb\n- ccc\n\U0001f600 \U0001f389 "
        text = body + "x" * (100 - len(body))
        out = self.hook(
            "pre_tool_use",
            self.payload(
                "pre_tool_use", tool_name=TOOL, tool_input={"text": text}, tool_use_id="t9"
            ),
        )
        context = self.context(out)
        self.assertIn("5x the median length", context)
        self.assertIn("3 bullets", context)
        self.assertIn("2 emoji", context)
        self.assertNotIn("permissionDecision", json.dumps(out))

    def test_text_the_user_typed_is_not_checked(self) -> None:
        self.seed(["x" * 20 for _ in range(15)])
        text = "- aaa\n- bbb\n- ccc " + "y" * 90
        self.prompt(f"post exactly this: {text}")
        out = self.hook(
            "pre_tool_use",
            self.payload("pre_tool_use", tool_name=TOOL, tool_input={"text": text}),
        )
        self.assertNotIn("Juno voice check", self.context(out))

    def test_other_tools_are_untouched(self) -> None:
        self.prompt("hello there")
        out = self.hook(
            "pre_tool_use",
            self.payload("pre_tool_use", tool_name="Bash", tool_input={"command": "ls"}),
        )
        self.assertNotIn("Juno voice", self.context(out))


if __name__ == "__main__":
    unittest.main()
