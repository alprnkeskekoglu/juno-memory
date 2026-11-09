import json
import time
from unittest import mock

from juno.adapters import cardjob, stubs
from tests.support.cardcase import CardCase, card
from tests.support.hooks import OTHER_SID, SID


class CardMasking(CardCase):
    def test_long_number_is_masked_and_recorded(self) -> None:
        self.dirty()
        text = card(body="Moved Jira 2169327 to done; see ABC-123456 and commit 1234567.")
        row = self.run_card(text)
        self.assertEqual((row["status"], row["error"]), ("ok", None))
        self.assertEqual(row["masked"], {"id": 1})
        written = self.handoff()
        self.assertIn("Moved Jira <id> to done", written)
        self.assertIn("ABC-123456", written)
        self.assertNotIn("2169327", written)
        self.assertEqual(cardjob.read_job(self.cfg(), SID)["masked"], {"id": 1})
        self.assertFalse(self.seg().dirty)
        self.assertNotIn("2169327", json.dumps(cardjob.read_runs(self.cfg())))

    def test_email_in_url_and_phone_are_masked(self) -> None:
        self.dirty()
        row = self.run_card(
            card(body="Opened https://app/u?email=jane.doe%40corp.com; call +49 170 1234567.")
        )
        self.assertEqual(row["masked"], {"email": 1, "phone": 1})
        self.assertIn("Opened <email> call <phone>.", self.handoff())

    def test_clean_card_has_no_masked_field(self) -> None:
        self.dirty()
        row = self.run_card(card())
        self.assertNotIn("masked", row)
        self.assertNotIn("masked", cardjob.read_job(self.cfg(), SID))

    def test_secret_card_is_still_dropped_even_with_a_number(self) -> None:
        self.dirty()
        row = self.run_card(card(body="id 2169327 key ghp_" + "b" * 36))
        self.assertEqual((row["status"], row["error"]), ("failed", "invalid: secret-like text"))
        self.assertNotIn("masked", row)
        self.assertEqual(self.handoff(), "")

    def test_masked_card_that_still_fails_is_dropped(self) -> None:
        self.dirty()
        row = self.run_card(card(body="User 2169327 said you should wait."))
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"].startswith("invalid: second person"))
        self.assertEqual(self.handoff(), "")

    def test_failed_job_is_named_in_the_stub_and_the_next_session_line(self) -> None:
        from juno.core import routing

        self.dirty()
        self.run_card(card(body="key ghp_" + "b" * 36))
        later = time.time() + 7200
        with mock.patch.object(cardjob, "spawn"):
            found = stubs.stub_ended_segments(self.cfg(), OTHER_SID, later)
        assert found is not None
        self.assertIn(
            "Card job failed (invalid: secret-like text); Juno wrote this stub", self.handoff()
        )
        self.assertNotIn("ended without a card", self.handoff())
        line = routing.previous_segment_line(
            *found, failure=cardjob.card_failure(self.cfg(), SID)
        )
        self.assertIn("card job failed (invalid: secret-like text)", line)

    def test_model_error_is_not_reported_as_an_invalid_card(self) -> None:
        self.dirty()
        job = self.job(status="failed", error="model run failed: boom")
        cardjob.write_job(self.cfg(), SID, job)
        self.assertIsNone(cardjob.card_failure(self.cfg(), SID))
