import json
import os
import re
import unittest
from typing import Any

from juno.adapters import learnio
from juno.core import selfguard, selfmodel
from tests.support.cardcase import CardCase
from tests.test_selfmodel import QUOTE, SOURCES, TEXT, item

VAULT_SELF = os.path.expanduser("~/juno-vault/self")
STANCE = "Believes Django is the best framework for any backend"
SENSITIVE = "Mentions depression when deadlines slip"


class Guard(unittest.TestCase):
    def problem(self, text: str, quote: str = QUOTE) -> str | None:
        return selfguard.content_problem(text, quote)

    def test_stances_are_refused(self) -> None:
        for text in (
            STANCE,
            "Thinks that microservices are wrong for small teams",
            "Thinks tabs are better than spaces in every project",
            "Is convinced that the rewrite was a mistake",
            "Holds strong opinions about semicolons",
            "Has a firm opinion on monorepos",
            "Is opinionated about naming",
        ):
            self.assertEqual(self.problem(text), "stance", text)

    def test_working_behaviour_is_not_a_stance(self) -> None:
        for text in (
            "Prefers short answers",
            "Wants a mockup first",
            "Rejects features that duplicate Claude Code",
            "Thinks in small steps and wants each one verified",
            "Wants an honest assessment before a decision",
            "Expects an explicit close instruction to be carried out directly",
        ):
            self.assertIsNone(self.problem(text), text)

    def test_stance_words_in_the_quote_are_fine(self) -> None:
        self.assertIsNone(self.problem(TEXT, "I think that the small fix is the better call"))

    def test_sensitive_text_or_quote_is_refused(self) -> None:
        for text in (
            SENSITIVE,
            "Skips meetings because of a therapy appointment",
            "Takes medication in the morning and starts late",
            "Avoids Friday work because of prayer time",
            "Follows politics closely during the election",
            "Worries about the mortgage and a salary cut",
            "Is going through a divorce so replies slowly",
            "Plans around the girlfriend's schedule",
            "Waits for a visa decision before moving",
            "Is pregnant and works shorter days",
            "Mental health days come first",
        ):
            self.assertEqual(self.problem(text), "sensitive", text)
        for quote in (
            "sağlık sorunlarım yüzünden geç cevap veririm bugün",
            "depresyondayım o yüzden kısa tutalım lütfen",
            "ilaçlarımı aldıktan sonra devam edelim şimdi",
            "namaz vakti ara vereceğim şimdi",
            "maaşım yatınca aboneliği alırım sonra",
            "kız arkadaşımla konuşup dönerim size",
            "hamile olduğum için erken çıkarım",
            "vize randevum var yarın ona gideceğim",
            "kredi borcum var, o yüzden kısa tutalım",
            "boşanma sürecindeyim, kısa cevap ver",
            "SAĞLIK sorunlarım var, kısa tut",
        ):
            self.assertEqual(self.problem(TEXT, quote), "sensitive", quote)

    def test_tech_uses_of_the_same_words_pass(self) -> None:
        for text in (
            "Adds a health check endpoint before shipping a service",
            "Asks for a healthcheck on every deploy",
            "Tracks technical debt in the backlog",
            "Treats a race condition as a blocker",
            "Prefers leader election to be tested first",
            "Reviews third-party party integrations last",
            "Wants the diagnose step to print the failing input",
        ):
            self.assertIsNone(self.problem(text), text)
        for quote in (
            "teknik borç listesine ekle ama önce testi çalıştır",
            "seçim kutusunu daha büyük yap lütfen",
            "bu partiyi tek seferde işleyelim lütfen şimdi",
            "health check endpoint'ini ekleyelim ve test edelim",
        ):
            self.assertIsNone(self.problem(TEXT, quote), quote)

    def test_check_reports_the_reason(self) -> None:
        for text, want in ((STANCE, "stance"), (SENSITIVE, "sensitive")):
            got = selfmodel.check(item(text=text), SOURCES, "none", 20)
            self.assertEqual(got, (want, {}), text)
        self.assertEqual(selfmodel.check(item(), SOURCES, "none", 20)[0], "ok")

    def test_real_vault_lines_pass(self) -> None:
        if not os.path.isdir(VAULT_SELF):
            self.skipTest("no vault")
        seen = 0
        for name in os.listdir(VAULT_SELF):
            with open(os.path.join(VAULT_SELF, name), encoding="utf-8") as handle:
                for row in handle:
                    match = re.match(r'- (.*?) · \S+ · src: user \S+ · "(.*)"$', row.strip())
                    if match:
                        seen += 1
                        self.assertIsNone(self.problem(*match.groups()), row)
        self.assertGreater(seen, 0)

    def test_header_reads_as_working_style(self) -> None:
        for word in ("working style", "agree", "push back"):
            self.assertIn(word, selfmodel.HEADER)
        self.assertLess(len(selfmodel.HEADER), 330)


class Learner(CardCase):
    def learn(self, extract: dict[str, object]) -> tuple[dict[str, Any], dict[str, str]]:
        self.dirty(QUOTE + ". now fix src/money.py")
        return learnio.apply(self.cfg(), self.seg(), json.dumps(extract), "digest", [])

    def test_instruction_states_both_rules(self) -> None:
        text = "\n".join(learnio.instructions(self.cfg(), self.seg()))
        self.assertIn("never an opinion or stance", text)
        self.assertIn("Never health", text)

    def test_stance_and_sensitive_lines_drop(self) -> None:
        for text, reason in ((STANCE, "stance"), (SENSITIVE, "sensitive")):
            _, sections = self.learn({"self": [item(text=text)]})
            self.assertEqual(sections["self"], f"dropped:{reason}", text)
        self.assertFalse(os.path.exists(self.vp("self/values.md")))
