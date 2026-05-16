import datetime
import os
import tempfile
import unittest
from unittest import mock

import memory_manager


class MemoryManagerTests(unittest.TestCase):
    def test_context_block_to_conversation_text_strips_display_metadata(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"[{timestamp}]\n"
            "Response ID: response_123\n"
            ">>> What did we discuss?\n"
            "[gpt-5.5 - high] We discussed rent roll formatting.\n\n"
            "### Token Usage\n\n"
            "`usage_cost`  \n"
            "input:10; output:5; total:15; estimated_cost:$0.0001\n\n"
            "Sources:\n"
            "[1] Example: https://example.test\n"
            "*Additional web-search sources were returned but not cited in the final answer.*\n"
            "Topic Tags: rent roll, formatting"
        )

        conversation = memory_manager.context_block_to_conversation_text(block)

        self.assertIn("User: What did we discuss?", conversation)
        self.assertIn("Assistant: We discussed rent roll formatting.", conversation)
        self.assertNotIn("Response ID", conversation)
        self.assertNotIn("Token Usage", conversation)
        self.assertNotIn("usage_cost", conversation)
        self.assertNotIn("Sources:", conversation)
        self.assertNotIn("Topic Tags", conversation)

    def test_prune_context_uses_sanitized_context_blocks(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"[{timestamp}]\n"
            "Response ID: response_123\n"
            ">>> Save the full visible answer.\n"
            "[gpt-5.5 - high] Full visible answer body.\n\n"
            "### Token Usage\n\n"
            "`usage_cost`  \n"
            "input:100; output:50; total:150; estimated_cost:$0.001\n"
            "Topic Tags: testing"
        )

        with tempfile.TemporaryDirectory() as directory:
            context_path = os.path.join(directory, "context.txt")
            permanent_path = os.path.join(directory, "permanent_memory.json")
            with open(context_path, "w", encoding="utf-8") as handle:
                handle.write(block + memory_manager.DELIMITER)
            with mock.patch.object(memory_manager, "CONTEXT_FILE", context_path):
                with mock.patch.object(memory_manager, "PERMANENT_MEMORY_FILE", permanent_path):
                    pruned, count, _, _ = memory_manager.prune_context(
                        "What was saved?",
                        model="gpt-5.5",
                        token_budget=1_000,
                    )

        self.assertEqual(count, 1)
        self.assertIn("User: Save the full visible answer.", pruned)
        self.assertIn("Assistant: Full visible answer body.", pruned)
        self.assertNotIn("Response ID", pruned)
        self.assertNotIn("Token Usage", pruned)
        self.assertNotIn("usage_cost", pruned)
        self.assertNotIn("Topic Tags", pruned)

    def test_prune_context_can_include_full_metadata_blocks(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"[{timestamp}]\n"
            "Response ID: response_123\n"
            ">>> Save the full visible answer.\n"
            "[gpt-5.5 - high] Full visible answer body.\n\n"
            "### Token Usage\n\n"
            "`usage_cost`  \n"
            "input:100; output:50; total:150; estimated_cost:$0.001\n\n"
            "Sources:\n"
            "[1] Example: https://example.test\n"
            "Topic Tags: testing"
        )

        with tempfile.TemporaryDirectory() as directory:
            context_path = os.path.join(directory, "context.txt")
            permanent_path = os.path.join(directory, "permanent_memory.json")
            with open(context_path, "w", encoding="utf-8") as handle:
                handle.write(block + memory_manager.DELIMITER)
            with mock.patch.object(memory_manager, "CONTEXT_FILE", context_path):
                with mock.patch.object(memory_manager, "PERMANENT_MEMORY_FILE", permanent_path):
                    pruned, count, _, _ = memory_manager.prune_context(
                        "What was saved?",
                        model="gpt-5.5",
                        token_budget=1_000,
                        include_metadata=True,
                    )

        self.assertEqual(count, 1)
        self.assertIn("Response ID: response_123", pruned)
        self.assertIn("### Token Usage", pruned)
        self.assertIn("`usage_cost`", pruned)
        self.assertIn("Sources:", pruned)
        self.assertIn("Topic Tags: testing", pruned)

    def test_add_to_context_preserves_full_visible_answer_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            context_path = os.path.join(directory, "context.txt")
            full_answer = (
                "Visible answer.\n\n"
                "### Token Usage\n\n"
                "`usage_cost`  \n"
                "input:1; output:1; total:2; estimated_cost:$0.0001\n\n"
                "Sources:\n"
                "[1] Example: https://example.test"
            )

            with mock.patch.object(memory_manager, "CONTEXT_FILE", context_path):
                memory_manager.add_to_context(
                    "Persist this.",
                    full_answer,
                    ["testing"],
                    reasoning_effort="low",
                    response_id="response_123",
                )

            with open(context_path, "r", encoding="utf-8") as handle:
                saved = handle.read()

        self.assertIn("Visible answer.", saved)
        self.assertIn("### Token Usage", saved)
        self.assertIn("`usage_cost`", saved)
        self.assertIn("Sources:", saved)
        self.assertIn("[1] Example: https://example.test", saved)


if __name__ == "__main__":
    unittest.main()
