import sys
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


if __name__ == "__main__":
    unittest.main()
