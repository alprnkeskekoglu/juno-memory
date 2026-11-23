import base64
import unittest

from juno.core import privacy
from juno.core.errors import Refused
from juno.core.redact import find_secrets, redact

ZW = "​"


class Misses(unittest.TestCase):
    def test_each_is_found(self) -> None:
        basic = base64.b64encode(b"user:password123").decode()
        for text in (
            "PRIVATE_KEY=abcdefghij12345678",
            "private-key: 'abcdefghij12345678'",
            f"sk{ZW}-abcdefghijklmnopqrstuvwx",
            f"gh{ZW}p_{'a' * 36}",
            f"API_KEY{chr(0x2060)}=abcdefghij12345678",
            'os.environ["API_TOKEN"] = "abcdefghij1234"',
            "os.environ['API_TOKEN']='abcdefghij1234'",
            "setx MY_API_KEY abcd1234efgh5678",
            "set -x MY_API_KEY abcd1234efgh5678",
            "set -gx GITHUB_TOKEN abcd1234efgh5678",
            f"Authorization: Basic {basic}",
        ):
            self.assertTrue(find_secrets(text), text)
            self.assertIn("[redacted:", redact(text), text)
            with self.assertRaises(Refused):
                privacy.check(text, "note")

    def test_redact_removes_the_value(self) -> None:
        basic = base64.b64encode(b"user:password123").decode()
        self.assertNotIn(basic, redact(f"Basic {basic} ok"))
        self.assertNotIn("abcd1234efgh5678", redact("setx MY_API_KEY abcd1234efgh5678"))
        self.assertNotIn("abcdefghij12345678", redact("PRIVATE_KEY=abcdefghij12345678"))
        self.assertNotIn("abcdefghijklmnopqrstuvwx", redact(f"sk{ZW}-abcdefghijklmnopqrstuvwx"))


class Negatives(unittest.TestCase):
    def test_prose_and_short_values_pass(self) -> None:
        for text in (
            "rotate the PRIVATE_KEY env var",
            "the PRIVATE_KEY is set in CI",
            "PRIVATE_KEY=short1",
            "PRIVATE_KEY=$PRIVATE_KEY",
            'os.environ["API_TOKEN"] = os.getenv("X")',
            'os.environ["API_TOKEN"] = "abc"',
            'os.environ["API_TOKEN"]',
            "tokens[0] = abcdefghij1234567890",
            "setx PATH C:\\tools",
            "setx MY_API_KEY short",
            "set -x PATH /usr/bin",
            "set -x MY_API_KEY $KEY",
            "pad_token = tokenizer.eos_token",
            'tokenizer.pad_token = "<pad>"',
            "eos_token=</s>",
            "pad_token_id=1234567890123",
            "Basic auth is deprecated here",
            "Basic dXNlcg== is just base64 of user",
            "Basic configuration for the Dockerfile",
            "Basic " + base64.b64encode(b"no colon here at all").decode(),
            "Basic " + base64.b64encode(bytes(range(200, 230))).decode(),
            "Basic abcdefghijklmnop",
            "zero\u200bwidth and emoji \U0001f468\u200d\U0001f4bb are fine",
        ):
            self.assertEqual(find_secrets(text), [], text)

    def test_text_without_secret_is_unchanged(self) -> None:
        for text in ("dev \U0001f468\u200d\U0001f4bb done", "rotate the PRIVATE_KEY env var"):
            self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
