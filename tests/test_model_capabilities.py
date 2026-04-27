import unittest

from model_capabilities import get_model_capabilities


class ModelCapabilityTests(unittest.TestCase):
    def test_gpt55_profile_has_directory_retrieval_capabilities(self):
        capabilities = get_model_capabilities("gpt-5.5")

        self.assertEqual(capabilities.max_context_tokens, 400_000)
        self.assertEqual(capabilities.max_output_tokens, 100_000)
        self.assertEqual(capabilities.safe_input_tokens, 250_000)
        self.assertTrue(capabilities.supports_file_search)
        self.assertTrue(capabilities.supports_web_search)
        self.assertIn("high", capabilities.reasoning_efforts)
        self.assertEqual(capabilities.default_directory_strategy, "file_search")

    def test_unknown_profile_has_conservative_fallback(self):
        capabilities = get_model_capabilities("unknown-model")

        self.assertEqual(capabilities.confidence, "fallback")
        self.assertFalse(capabilities.supports_file_search)
        self.assertLess(capabilities.safe_input_tokens, capabilities.max_context_tokens)


if __name__ == "__main__":
    unittest.main()
