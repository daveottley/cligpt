import sys
import io
import contextlib
import unittest
from unittest import mock

import cli_interface


class CliInterfaceTests(unittest.TestCase):
    def test_default_query_sends_context_by_default(self):
        argv = [
            "cligpt.py",
            "--no-web",
            "Use normal context.",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "query")
        self.assertTrue(args.include_context)
        self.assertFalse(args.web_search)
        self.assertEqual(args.prompt, "Use normal context.")

    def test_default_query_accepts_no_context_flag(self):
        argv = [
            "cligpt.py",
            "--no-context",
            "--no-web",
            "What is the smallest request?",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "query")
        self.assertFalse(args.include_context)
        self.assertFalse(args.web_search)
        self.assertEqual(args.prompt, "What is the smallest request?")

    def test_default_query_accepts_raw_flag(self):
        argv = [
            "cligpt.py",
            "--raw",
            "What is the raw minimum?",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "query")
        self.assertTrue(args.raw_prompt)
        self.assertTrue(args.include_context)
        self.assertFalse(args.full_context)
        self.assertEqual(args.prompt, "What is the raw minimum?")

    def test_default_query_accepts_full_context_flag(self):
        argv = [
            "cligpt.py",
            "--full-context",
            "--no-web",
            "Use full context.",
        ]
        with mock.patch.object(sys, "argv", argv):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "query")
        self.assertTrue(args.include_context)
        self.assertTrue(args.full_context)
        self.assertFalse(args.web_search)
        self.assertEqual(args.prompt, "Use full context.")

    def test_remember_help_omits_query_only_flags(self):
        argv = [
            "cligpt.py",
            "--width",
            "79",
            "remember",
            "--help",
        ]
        stdout = io.StringIO()

        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    cli_interface.parse_args()

        help_text = stdout.getvalue()
        self.assertIn("remember [-h] text", help_text)
        self.assertIn("Memory in 'key: value' format", help_text)
        self.assertNotIn("--directory", help_text)
        self.assertNotIn("--file", help_text)
        self.assertNotIn("--raw", help_text)

    def test_top_level_help_is_concise_command_index(self):
        stdout = io.StringIO()

        with mock.patch.object(sys, "argv", ["cligpt.py", "--help"]):
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    cli_interface.parse_args()

        help_text = stdout.getvalue()
        self.assertIn("usage: gpt command [args]", help_text)
        self.assertIn("CLI Help Agent with context/memory management, web search, and tool use", help_text)
        self.assertIn("commands:", help_text)
        self.assertIn("  query", help_text)
        self.assertIn("  remember", help_text)
        self.assertIn('Options vary per command. Run "gpt command --help" for detailed options.', help_text)
        self.assertNotIn("{query,remember", help_text)
        self.assertNotIn("positional arguments:", help_text)
        self.assertNotIn("Available subcommands", help_text)
        self.assertNotIn("--directory", help_text)

    def test_memory_aliases_parse(self):
        with mock.patch.object(sys, "argv", ["cligpt.py", "forget", "3"]):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "forget")
        self.assertEqual(args.id, 3)

        with mock.patch.object(sys, "argv", ["cligpt.py", "edit-memory", "3", "name: David"]):
            args = cli_interface.parse_args()

        self.assertEqual(args.command, "edit-memory")
        self.assertEqual(args.id, 3)
        self.assertEqual(args.text, "name: David")


if __name__ == "__main__":
    unittest.main()
