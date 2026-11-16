import unittest

from tests.e2e import harness


class Defaults(unittest.TestCase):
    def test_model_defaults_to_haiku_and_env_overrides(self) -> None:
        self.assertEqual(harness.default_model({}), "haiku")
        self.assertEqual(harness.default_model({"JUNO_E2E_MODEL": " "}), "haiku")
        self.assertEqual(harness.default_model({"JUNO_E2E_MODEL": "opus"}), "opus")

    def test_budget_is_half_dollar_for_haiku_and_two_for_others(self) -> None:
        self.assertEqual(harness.budget_for("haiku", {}), 0.5)
        self.assertEqual(harness.budget_for("claude-haiku-4-5", {}), 0.5)
        self.assertEqual(harness.budget_for("sonnet", {}), 2.0)
        self.assertEqual(harness.budget_for("opus", {}), 2.0)

    def test_budget_env_overrides_every_model(self) -> None:
        env = {"JUNO_E2E_BUDGET": "3.5"}
        self.assertEqual(harness.budget_for("haiku", env), 3.5)
        self.assertEqual(harness.budget_for("opus", env), 3.5)


class BudgetStop(unittest.TestCase):
    def test_budget_subtype_is_a_stop(self) -> None:
        event = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True}
        self.assertTrue(harness.stopped_on_budget([event], 0.5))

    def test_error_at_or_over_cap_is_a_stop(self) -> None:
        event = {"type": "result", "subtype": "error_during_execution", "is_error": True}
        self.assertTrue(harness.stopped_on_budget([{**event, "total_cost_usd": 0.52}], 0.5))
        self.assertFalse(harness.stopped_on_budget([{**event, "total_cost_usd": 0.1}], 0.5))

    def test_success_is_not_a_stop(self) -> None:
        event = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.9}
        self.assertFalse(harness.stopped_on_budget([{"type": "system"}, event], 0.5))

    def test_message_names_cap_and_env(self) -> None:
        text = harness.budget_message(0.5)
        self.assertIn("$0.5", text)
        self.assertIn("JUNO_E2E_BUDGET", text)


if __name__ == "__main__":
    unittest.main()
