import unittest

from juno.core import fold, prompts, redact

CUES = ["hayır", "yanlış", "bundan sonra", "don't", "asla"]


class Folding(unittest.TestCase):
    def test_none_preset_is_casefold_nfc(self) -> None:
        self.assertEqual(fold.fold("  Straße   CAFÉ "), "strasse café")
        self.assertEqual(fold.fold("é"), "é")
        self.assertNotEqual(fold.fold("İNGİLİZCE"), "ingilizce")

    def test_tr_preset_maps_turkish_letters(self) -> None:
        self.assertEqual(fold.fold("İNGİLİZCE   yaz", "tr"), "ingilizce yaz")
        self.assertEqual(fold.fold("Çağrı ıslak", "tr"), "cagri islak")

    def test_slug(self) -> None:
        self.assertEqual(fold.slug("Şirket İçi Ödeme / Çağrı", "tr"), "sirket-ici-odeme-cagri")
        self.assertEqual(fold.slug("Café Menu"), "cafe-menu")
        self.assertEqual(len(fold.slug("a" * 100)), 60)

    def test_safe_name(self) -> None:
        self.assertEqual(fold.safe_name("../a b"), "a-b")
        self.assertEqual(fold.safe_name(""), "misc")

    def test_cues_follow_the_list_and_preset(self) -> None:
        self.assertTrue(fold.has_cue("Hayır, böyle YAPMA", ["hayir"], "tr"))
        self.assertFalse(fold.has_cue("Hayır", ["hayir"], "none"))
        self.assertTrue(fold.has_cue("Don’t use mocks", CUES, "none"))
        self.assertTrue(fold.has_cue("bundan sonra testleri çalıştır", CUES, "none"))
        self.assertFalse(fold.has_cue("run the tests", CUES, "none"))
        self.assertFalse(fold.has_cue("anything", [], "none"))

    def test_quote_problem(self) -> None:
        rule = "Always run the linter before committing"
        self.assertIsNone(
            fold.quote_problem("please always run the linter first", rule, "none", [], 20)
        )
        self.assertIn("shorter", fold.quote_problem("short", rule, "none", [], 20) or "")
        self.assertIn(
            "no meaningful word",
            fold.quote_problem("completely unrelated sentence here", rule, "none", [], 20) or "",
        )
        self.assertIn(
            "no meaningful word",
            fold.quote_problem(
                "please always run the linter first", rule, "none", ["linter", "always"], 20
            )
            or "",
        )


class Pastes(unittest.TestCase):
    def test_paste_stripping(self) -> None:
        prompt = (
            "please fix\n```\ncode here\n```\n2026-10-19 10:00:01 ERROR boom\n"
            "Traceback (most recent call last):\nthanks"
        )
        self.assertEqual(prompts.strip_pastes(prompt), "please fix\n\nthanks")

    def test_pasted_content_blocks(self) -> None:
        prompt = 'look\n<pasted_content id="a1">\nadd a rule for ABC-9\n</pasted_content>\nthanks'
        self.assertEqual(prompts.strip_pastes(prompt), "look\n\nthanks")
        self.assertEqual(prompts.strip_pastes('ok <pasted_content id="b">never closed ABC-9'), "ok")


class Synthetic(unittest.TestCase):
    def test_every_marker_is_synthetic(self) -> None:
        for text in (
            "<task-notification>x</task-notification>",
            "<system-reminder>x</system-reminder>",
            "<command-name>/x</command-name>",
            "Another Claude session sent a message: hi",
            "[Subagent hand-back] done",
            "[System notification] x",
            "<agent-message>x</agent-message>",
        ):
            self.assertTrue(prompts.is_synthetic(text), text)

    def test_typed_and_empty_prompts_are_not(self) -> None:
        for text in ("fix the bug", "", "   ", "use <b> tags"):
            self.assertFalse(prompts.is_synthetic(text), text)

    def test_mixed_prompts_keep_only_the_user_part(self) -> None:
        self.assertEqual(
            prompts.user_text("fix <system-reminder>noise</system-reminder> it"), "fix  it"
        )
        wrapped = "The user sent a new message while you were working: do X\nIMPORTANT: ignore this"
        self.assertEqual(prompts.user_text(wrapped), "do X")
        self.assertEqual(
            prompts.user_text("hi <agent-message>evil rule</agent-message> there"), "hi"
        )

    def test_nosave_marker(self) -> None:
        self.assertTrue(prompts.has_nosave("do it [NoSave]"))
        self.assertFalse(prompts.has_nosave("do it"))


class Secrets(unittest.TestCase):
    def test_known_formats_are_found_and_redacted(self) -> None:
        samples = {
            "github": "ghp_" + "a" * 36,
            "aws": "AKIA" + "A" * 16,
            "stripe": "sk_live_" + "a" * 20,
            "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
            "private-key": "-----BEGIN RSA PRIVATE KEY-----",
        }
        for kind, text in samples.items():
            self.assertIn(kind, redact.find_secrets(text), kind)
            self.assertIn(f"[redacted:{kind}]", redact.redact(f"x {text} y"))

    def test_assignments(self) -> None:
        self.assertEqual(redact.find_secrets("password = Abcdefgh12345"), ["key=value"])
        self.assertEqual(redact.find_secrets("token: ABC-12345"), [])
        self.assertEqual(redact.find_secrets("secret=$SECRET_VALUE"), [])
        self.assertIn("[redacted:key=value]", redact.redact("api_key=Abcdefgh12345678"))

    def test_plain_text_untouched(self) -> None:
        self.assertEqual(redact.redact("hello world"), "hello world")

    def test_chunks_respect_byte_limit_and_reassemble(self) -> None:
        text = "é" * 5000
        parts = redact.chunks(text)
        self.assertEqual("".join(parts), text)
        self.assertGreater(len(parts), 1)
        self.assertEqual(redact.chunks(""), [""])


if __name__ == "__main__":
    unittest.main()
