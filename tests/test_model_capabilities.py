import unittest
from unittest import mock

from model_capabilities import (
    OFFICIAL_MODEL_COMPARE_URL,
    get_model_capabilities,
    get_recent_history_token_budget,
    parse_model_compare_html,
    safe_input_from_limits,
)


class ModelCapabilityTests(unittest.TestCase):
    def test_gpt55_profile_has_directory_retrieval_capabilities(self):
        with mock.patch("model_capabilities.load_official_model_limits", return_value={}):
            capabilities = get_model_capabilities("gpt-5.5")

        self.assertEqual(capabilities.max_context_tokens, 1_050_000)
        self.assertEqual(capabilities.max_output_tokens, 128_000)
        self.assertEqual(capabilities.safe_input_tokens, safe_input_from_limits(1_050_000, 128_000))
        self.assertTrue(capabilities.supports_file_search)
        self.assertTrue(capabilities.supports_web_search)
        self.assertIn("high", capabilities.reasoning_efforts)
        self.assertEqual(capabilities.default_directory_strategy, "file_search")
        self.assertEqual(capabilities.input_price_per_million, 5.00)
        self.assertEqual(capabilities.cached_input_price_per_million, 0.50)
        self.assertEqual(capabilities.output_price_per_million, 30.00)

    def test_unknown_profile_has_conservative_fallback(self):
        with mock.patch("model_capabilities.load_official_model_limits", return_value={}):
            capabilities = get_model_capabilities("unknown-model")

        self.assertEqual(capabilities.confidence, "fallback")
        self.assertFalse(capabilities.supports_file_search)
        self.assertLess(capabilities.safe_input_tokens, capabilities.max_context_tokens)

    def test_official_model_limits_override_cached_profile(self):
        official = {
            "gpt-5.5": {
                "display_name": "GPT-5.5",
                "max_context_tokens": 1_050_000,
                "max_output_tokens": 128_000,
                "input_price_per_million": 5.00,
                "cached_input_price_per_million": 0.50,
                "output_price_per_million": 30.00,
                "source_url": OFFICIAL_MODEL_COMPARE_URL,
            }
        }

        with mock.patch("model_capabilities.load_official_model_limits", return_value=official):
            capabilities = get_model_capabilities("gpt-5.5")

        self.assertEqual(capabilities.confidence, "official-openai-docs-cache")
        self.assertEqual(capabilities.max_context_tokens, 1_050_000)
        self.assertEqual(capabilities.max_output_tokens, 128_000)
        self.assertEqual(capabilities.safe_input_tokens, safe_input_from_limits(1_050_000, 128_000))

    def test_recent_history_budget_depends_on_model_safe_input(self):
        with mock.patch("model_capabilities.load_official_model_limits", return_value={}):
            gpt55_budget = get_recent_history_token_budget("gpt-5.5")
            gpt4o_budget = get_recent_history_token_budget("gpt-4o")

        self.assertGreater(gpt55_budget, gpt4o_budget)
        self.assertLessEqual(gpt55_budget, 120_000)

    def test_parse_model_compare_html_extracts_limits(self):
        html = """
        <h2>GPT-5.5</h2>
        <p>Pricing</p><p>Per 1M tokens</p><p>Input</p><p>$5.00</p>
        <p>Cached Input</p><p>$0.50</p><p>Output</p><p>$30.00</p>
        <p>Context</p><p>Window</p><p>1,050,000</p>
        <p>Max Output Tokens</p><p>128,000</p>
        """

        parsed = parse_model_compare_html(html)

        self.assertEqual(parsed["gpt-5.5"]["max_context_tokens"], 1_050_000)
        self.assertEqual(parsed["gpt-5.5"]["max_output_tokens"], 128_000)
        self.assertEqual(parsed["gpt-5.5"]["cached_input_price_per_million"], 0.50)


if __name__ == "__main__":
    unittest.main()
