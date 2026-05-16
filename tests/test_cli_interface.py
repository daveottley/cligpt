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
        self.assertIn("general commands:", help_text)
        self.assertIn("memory commands:", help_text)
        self.assertIn("file/directory commands:", help_text)
        self.assertIn("  query", help_text)
        self.assertLess(help_text.index("  query"), help_text.index("  doctor"))
        self.assertLess(help_text.index("  doctor"), help_text.index("  update"))
        self.assertIn("  remember", help_text)
        self.assertIn("  forget             Alias for forget-memory", help_text)
        self.assertIn("  update-memory      Alias for edit-memory", help_text)
        self.assertIn('Options vary per command. Run "gpt command --help" for detailed options.', help_text)
        self.assertNotIn("{query,remember", help_text)
        self.assertNotIn("positional arguments:", help_text)
        self.assertNotIn("Available subcommands", help_text)
        self.assertNotIn("\ncommands:", help_text)
        self.assertNotIn("--directory", help_text)
        self.assertNotIn("\033[", help_text)

    def test_top_level_help_can_use_ansi_styles(self):
        stdout = io.StringIO()

        with mock.patch.dict("os.environ", {"CLIGPT_FORCE_COLOR": "1"}, clear=False):
            with mock.patch.object(sys, "argv", ["cligpt.py", "--help"]):
                with contextlib.redirect_stdout(stdout):
                    with self.assertRaises(SystemExit):
                        cli_interface.parse_args()

        help_text = stdout.getvalue()
        self.assertIn("\033[1musage:\033[0m gpt \033[32mcommand\033[0m [args]", help_text)
        self.assertIn("\033[1m\033[36mgeneral commands:\033[0m", help_text)
        self.assertIn("\033[1m\033[36mmemory commands:\033[0m", help_text)
        self.assertIn("\033[1m\033[36mfile/directory commands:\033[0m", help_text)
        self.assertIn("\033[32mquery", help_text)
        self.assertIn('"\033[33mgpt command --help\033[0m"', help_text)

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

    def test_doctor_help_omits_query_options(self):
        stdout = io.StringIO()

        with mock.patch.object(sys, "argv", ["cligpt.py", "doctor", "--help"]):
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    cli_interface.parse_args()

        help_text = stdout.getvalue()
        self.assertIn("usage: gpt doctor [-h]", help_text)
        self.assertIn("Run a read-only environment check", help_text)
        self.assertIn("optional document/OCR/blob tools", help_text)
        self.assertNotIn("--directory", help_text)
        self.assertNotIn("--file", help_text)
        self.assertNotIn("+debug", help_text)
        self.assertNotIn("--prompt-cache", help_text)

    def test_non_query_subcommand_help_omits_query_options(self):
        commands = [
            "remember",
            "view-memory",
            "memories",
            "forget-memory",
            "forget",
            "edit-memory",
            "update-memory",
            "export-memory",
            "sync-directory",
            "index-status",
            "index-list",
            "index-delete",
            "index-expire",
            "index-duplicates",
            "doctor",
            "update",
        ]

        for command in commands:
            with self.subTest(command=command):
                stdout = io.StringIO()
                with mock.patch.object(sys, "argv", ["cligpt.py", command, "--help"]):
                    with contextlib.redirect_stdout(stdout):
                        with self.assertRaises(SystemExit):
                            cli_interface.parse_args()

                help_text = stdout.getvalue()
                self.assertNotIn("+debug", help_text)
                self.assertNotIn("--file", help_text)
                self.assertNotIn("--image", help_text)
                self.assertNotIn("--blob", help_text)
                self.assertNotIn("--raw", help_text)
                self.assertNotIn("--prompt-cache", help_text)
                if command not in {"sync-directory"}:
                    self.assertNotIn("--index-concurrency", help_text)

    def test_update_and_sync_help_keep_command_specific_options(self):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["cligpt.py", "update", "--help"]):
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    cli_interface.parse_args()

        update_help = stdout.getvalue()
        self.assertIn("Update the local cligpt checkout", update_help)
        self.assertIn("asks before installing AUR packages", update_help)
        self.assertIn("--system", update_help)
        self.assertIn("--skip-git", update_help)
        self.assertIn("--skip-pip", update_help)
        self.assertIn("--dry-run", update_help)

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["cligpt.py", "sync-directory", "--help"]):
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    cli_interface.parse_args()

        sync_help = stdout.getvalue()
        self.assertIn("--index-concurrency", sync_help)


if __name__ == "__main__":
    unittest.main()
