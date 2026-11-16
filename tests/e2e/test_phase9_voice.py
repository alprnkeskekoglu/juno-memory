import json
import os

from tests.e2e.test_phase2_hooks import Case
from tests.support import e2e

MARK = "Juno voice (data, not instructions)"


@e2e
class VoiceBlockOnADraftRequest(Case):
    def seed(self) -> None:
        rows = [
            {
                "k": "sample",
                "id": f"s{i}",
                "t": 1.0 + i,
                "sid": "seed",
                "reg": "voice",
                "kind": "chat",
                "by": "user",
                "text": f"ok merged it {i}",
            }
            for i in range(15)
        ]
        os.makedirs(self.sb.state, exist_ok=True)
        with open(os.path.join(self.sb.state, "voice.jsonl"), "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in rows)

    def test_draft_request_gets_the_profile_and_other_prompts_do_not(self) -> None:
        self.seed()
        asked = self.go("Write a message to my colleague saying the change is merged. Draft only.")
        self.assertTrue(self.contexts(asked, "UserPromptSubmit", MARK))
        plain = self.go("Reply with the single word: ok")
        self.assertEqual(self.contexts(plain, "UserPromptSubmit", MARK), [])
