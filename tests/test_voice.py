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

    def test_example_filter_skips_known_names_in_any_case_and_position(self) -> None:
        keep = voicemeasure.clean_example
        names = frozenset({"anna", "bertram"})
        self.assertFalse(keep("anna said it is fine", None, names))
        self.assertFalse(keep("Bertram, merged it", None, names))
        self.assertFalse(keep("ok merged it, ask bertram", None, names))
        self.assertTrue(keep("ok merged it", None, names))
        self.assertTrue(keep("annals of merging", None, names))

    def test_name_tokens_take_mentions_and_mid_sentence_capitals_only(self) -> None:
        found = voicemeasure.name_tokens("Merged it. Ask Cornelius and @dora about I'm ok. Done.")
        self.assertEqual(found, {"cornelius", "dora"})

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

    def finish(self, texts: list[str]) -> str:
        for text in texts:
            self.prompt(f"post this message: {text}")
            self.post(text)
        self.hook("session_end", self.payload("session_end", reason="other"))
        with open(os.path.join(self.sb.vault, "self", "voice.md"), encoding="utf-8") as handle:
            return handle.read()

    def test_known_names_never_become_examples(self) -> None:
        profile = os.path.join(self.sb.vault, "projects", "demo.md")
        os.makedirs(os.path.dirname(profile), exist_ok=True)
        with open(profile, "w", encoding="utf-8") as handle:
            handle.write("## Purpose\nPaired with Bertram on the rollout.\n")
        self.set_config('[privacy]\nnames = ["zed"]\n')
        clean = [f"ok merged it {chr(97 + i)}" for i in range(12)]
        body = self.finish(
            [
                *clean,
                "bertram thinks it is fine",
                "zed is on it",
                "ok ask Cornelius",
                "cornelius ok",
            ]
        )
        self.assertIn("## voice (16 samples)", body)
        for name in ("bertram", "zed", "cornelius"):
            self.assertNotIn(name, body.casefold())
        self.assertIn("ok merged it", body)

    def test_names_directory_stays_out_of_the_vault(self) -> None:
        self.finish([f"ok merged it {chr(97 + i)}" for i in range(14)] + ["hey Cornelius ok"])
        for base, _dirs, files in os.walk(self.sb.vault):
            for name in files:
                with open(os.path.join(base, name), encoding="utf-8", errors="replace") as handle:
                    self.assertNotIn("cornelius", handle.read().casefold(), name)

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

    def test_voice_cue_switches_back_after_a_formal_draft(self) -> None:
        self.seed_both()
        formal = self.prompt("write a message to him about the merge, formal")
        self.assertIn("Register: formal", formal)
        context = self.prompt("casual")
        self.assertIn("Register: voice", context)
        self.assertIn("yo merged", context)
        self.assertNotIn("Thank you, the change", context)

    def test_voice_cue_alone_does_nothing_before_a_draft_request(self) -> None:
        self.seed_both()
        self.assertNotIn("Juno voice", self.prompt("casual"))

    def test_formal_is_one_shot_and_the_next_post_is_checked_as_voice(self) -> None:
        self.seed_both()
        long_text = "- aaa\n- bbb\n- ccc " + "y" * 120
        self.assertIn(
            "Register: formal", self.prompt("write a message to him about the merge, formal")
        )
        first = self.hook(
            "pre_tool_use",
            self.payload("pre_tool_use", tool_name=TOOL, tool_input={"text": long_text}),
        )
        self.assertIn("register formal", self.context(first))
        self.post(long_text)
        second = self.hook(
            "pre_tool_use",
            self.payload("pre_tool_use", tool_name=TOOL, tool_input={"text": long_text}),
        )
        self.assertIn("register voice", self.context(second))
        self.assertNotIn("Register:", self.context(second))
        self.post(long_text)
        self.assertEqual([s["reg"] for s in self.events("voice")], ["formal", "voice"])

    def test_formal_cue_without_a_draft_request_switches_nothing(self) -> None:
        self.seed_both()
        self.assertNotIn("Juno voice", self.prompt("make it formal"))
        self.assertEqual(self.events("voice_ask"), [])
        self.prompt("write a message to him")
        self.assertEqual([e["reg"] for e in self.events("voice_ask")], ["voice"])

    def test_explicit_voice_cues_replace_the_preset(self) -> None:
        self.set_config('[voice]\nvoice_cues = ["plainly"]\n')
        self.seed_both()
        self.prompt("write a message to him, formal")
        self.assertNotIn("Juno voice", self.prompt("casual"))
        self.assertIn("Register: voice", self.prompt("plainly"))

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
        self.assertIn("Register: formal", self.prompt("resmi yaz"))
        self.assertIn("Register: voice", self.prompt("rahat"))


class Targets(VoiceCase):
    LONG = "- aaa\n- bbb\n- ccc " + "y" * 120

    def setUp(self) -> None:
        super().setUp()
        self.seed([f"yo merged {i}" for i in range(15)], "voice")
        self.seed([f"Thank you, the change {i} is merged." for i in range(15)], "formal")
        self.prompt("hello there")

    def check(self, tool: str, tool_in: dict[str, Any]) -> str:
        out = self.hook(
            "pre_tool_use", self.payload("pre_tool_use", tool_name=tool, tool_input=tool_in)
        )
        return self.context(out)

    def bash(self, command: str) -> str:
        return self.check("Bash", {"command": command})

    def formal_calls(self) -> list[tuple[str, dict[str, Any]]]:
        path = os.path.join(self.sb.root, "body.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.LONG)
        return [
            ("Bash", {"command": f"gh issue create --title t --body-file {path}"}),
            ("Bash", {"command": f"gh pr create --title t --body '{self.LONG}'"}),
            ("mcp__tracker__create_issue", {"description": self.LONG}),
            ("mcp__tracker__add_comment", {"body": self.LONG}),
        ]

    def voice_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            ("Bash", {"command": f"gh pr comment 4 --body '{self.LONG}'"}),
            ("Bash", {"command": f"gh pr review 4 --comment --body '{self.LONG}'"}),
            (TOOL, {"text": self.LONG}),
        ]

    def test_code_host_bodies_and_tracker_writes_default_to_formal(self) -> None:
        for tool, tool_in in self.formal_calls():
            with self.subTest(tool=tool, tool_in=tool_in):
                self.assertIn("register formal", self.check(tool, tool_in))

    def test_formal_default_reaches_the_collected_sample(self) -> None:
        calls = self.formal_calls()
        for n, (tool, tool_in) in enumerate(calls):
            self.hook(
                "post_tool_use",
                self.payload(
                    "post_tool_use",
                    tool_name=tool,
                    tool_input=tool_in,
                    tool_response={},
                    tool_use_id=f"t{n}",
                ),
            )
        self.assertEqual([s["reg"] for s in self.events("voice")], ["formal"] * len(calls))

    def test_pr_comments_review_notes_and_chat_default_to_voice(self) -> None:
        for tool, tool_in in self.voice_calls():
            with self.subTest(tool=tool):
                self.assertIn("register voice", self.check(tool, tool_in))

    def test_formal_cue_in_the_request_wins_over_a_voice_target(self) -> None:
        self.prompt("write a message to him about the merge, formal")
        self.assertIn("register formal", self.check(TOOL, {"text": self.LONG}))

    def test_voice_cue_in_the_request_wins_over_a_formal_target(self) -> None:
        self.prompt("write a comment for this issue, in my voice")
        out = self.check("mcp__tracker__add_comment", {"body": self.LONG})
        self.assertIn("register voice", out)

    def test_formal_targets_config_replaces_the_default_list(self) -> None:
        self.set_config('[voice]\nformal_targets = ["mcp__*__send*message*"]\n')
        self.assertIn("register formal", self.check(TOOL, {"text": self.LONG}))
        self.assertIn(
            "register voice", self.bash(f"gh issue create --title t --body '{self.LONG}'")
        )

    def test_default_formal_targets_name_no_product_and_use_outward_syntax(self) -> None:
        from juno import config

        voice = config.DEFAULTS["voice"]
        text = json.dumps(voice["formal_targets"]).casefold()
        for word in ("slack", "jira", "atlassian", "linear", "github", *local_banned()):
            self.assertNotIn(word, text)
        for pattern in voice["formal_targets"]:
            self.assertIn(pattern, voice["outward_tools"])


class DraftRequest(VoiceCase):
    def test_talk_about_registers_injects_nothing(self) -> None:
        self.seed([f"yo merged {i}" for i in range(15)])
        text = "tracker comment can be formal, but chat should stay in my voice"
        self.assertEqual(self.prompt(text), "")
        self.assertEqual(self.events("voice_ask"), [])

    def test_write_verb_aimed_at_a_target_injects(self) -> None:
        self.assertIn("Juno voice", self.prompt("write a tracker comment for this"))
        self.assertIn("Juno voice", self.prompt("send him a quick note"))

    def test_turkish_standing_preference_injects_nothing(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        text = (
            "her zaman rahat bir dille taslak hazırlasın ben formal olduğunu belirtirim bir şekilde"
        )
        self.assertNotIn("Juno voice", self.prompt(text))
        self.assertNotIn("Juno voice", self.prompt("taslak hazırlasın"))
        self.assertEqual(self.events("voice_ask"), [])

    def test_turkish_requests_still_inject(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.assertIn("Juno voice", self.prompt("şu issue'ya bir yorum yaz"))
        self.assertIn("Juno voice", self.prompt("ona bir taslak hazırla"))

    def test_standing_preference_does_not_switch_the_register(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.prompt("ona bir mesaj yaz")
        self.assertNotIn("Juno voice", self.prompt("hep rahat yaz, formal olursa ben söylerim"))


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
