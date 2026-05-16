import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import ai_client
from local_search import LocalSearchIndex
from render import RenderConfig, TerminalRenderer


class UploadAttachmentTests(unittest.TestCase):
    def make_pdf(self, directory, name):
        path = os.path.join(directory, name)
        with open(path, "wb") as f:
            f.write(b"%PDF-1.7\n%%EOF\n")
        return path

    def test_multi_upload_skips_failed_file_and_returns_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_pdf = self.make_pdf(directory, "bad.pdf")
            good_pdf = self.make_pdf(directory, "good.pdf")

            def fake_upload(upload):
                if upload["path"] == bad_pdf:
                    raise ValueError("simulated connection failure")
                return {
                    "path": upload["path"],
                    "kind": upload["kind"],
                    "file_id": "file_good",
                }

            stderr = io.StringIO()
            with mock.patch.object(ai_client, "compress_pdf_for_upload", side_effect=lambda path, _: path):
                with mock.patch.object(ai_client, "upload_file_for_response", side_effect=fake_upload):
                    with contextlib.redirect_stderr(stderr):
                        attachments = ai_client.upload_attachments(file_paths=[bad_pdf, good_pdf])

            self.assertEqual(len(attachments), 2)
            self.assertEqual(attachments[0]["file_id"], "file_good")
            self.assertEqual(attachments[1]["path"], "cligpt upload failures")
            self.assertIn("simulated connection failure", attachments[1]["text"])
            self.assertIn("Uploads 2/2", stderr.getvalue())
            self.assertIn("Upload skipped after failure", stderr.getvalue())

    def test_single_upload_failure_still_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_pdf = self.make_pdf(directory, "bad.pdf")

            stderr = io.StringIO()
            with mock.patch.object(ai_client, "compress_pdf_for_upload", side_effect=lambda path, _: path):
                with mock.patch.object(
                    ai_client,
                    "upload_file_for_response",
                    side_effect=ValueError("simulated connection failure"),
                ):
                    with contextlib.redirect_stderr(stderr):
                        with self.assertRaises(ValueError):
                            ai_client.upload_attachments(file_paths=[bad_pdf])

            self.assertIn("Uploads 1/1", stderr.getvalue())

    def test_directory_pdf_is_not_directly_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_pdf(directory, "indexed.pdf")

            with mock.patch.object(ai_client, "upload_file_for_response") as upload_file:
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    attachments = ai_client.upload_attachments(directory_paths=[directory])

            upload_file.assert_not_called()
            self.assertEqual(attachments, [])

    def test_directory_image_is_not_direct_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "image.png")
            with open(image_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")

            with mock.patch.object(
                ai_client,
                "upload_file_for_response",
                return_value={"path": image_path, "kind": "image", "file_id": "file_image"},
            ) as upload_file:
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    attachments = ai_client.upload_attachments(directory_paths=[directory])

            upload_file.assert_not_called()
            self.assertEqual(attachments, [])

    def test_pdf_search_preparation_prefers_extracted_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = self.make_pdf(directory, "lease.pdf")
            with mock.patch.object(ai_client, "extract_pdf_text_layer", return_value="rent " * 200):
                with mock.patch.object(ai_client, "get_pdf_page_count", return_value=1):
                    output_path = ai_client.prepare_pdf_text_for_search(pdf_path, directory)

            self.assertTrue(output_path.endswith(".pdf-text.txt"))
            with open(output_path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("Extraction method: pdftotext text layer", text)
            self.assertIn("rent rent", text)

    def test_directory_manifest_marks_old_folder_as_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            active_dir = os.path.join(directory, "1407 Bundrant")
            old_dir = os.path.join(directory, "Old", "1405 N Gray")
            os.makedirs(active_dir)
            os.makedirs(old_dir)
            active_pdf = self.make_pdf(active_dir, "current.pdf")
            old_pdf = self.make_pdf(old_dir, "former.pdf")
            uploads = [
                {"path": active_pdf, "kind": "file", "root_path": directory},
                {"path": old_pdf, "kind": "file", "root_path": directory},
            ]

            manifest = ai_client.directory_manifest(directory, uploads)

            self.assertIn("1407 Bundrant: active candidate", manifest)
            self.assertIn("Old: archive/disposed candidate", manifest)
            self.assertIn("exclude them from current rent rolls", manifest)

    def test_search_preparation_stamps_archive_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            old_dir = os.path.join(directory, "Old", "Garrison")
            os.makedirs(old_dir)
            text_path = os.path.join(old_dir, "lease.txt")
            with open(text_path, "w", encoding="utf-8") as handle:
                handle.write("rent is 750")
            upload = {"path": text_path, "kind": "text", "root_path": directory}

            output_path = ai_client.prepare_upload_path_for_search(upload, directory)

            with open(output_path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("Directory classification: archive/disposed candidate", text)
            self.assertIn("Current-report handling: exclude from current operating reports", text)
            self.assertIn("rent is 750", text)

    def test_document_image_is_ocr_indexed_for_search(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "lease.jpg")
            with open(image_path, "wb") as handle:
                handle.write(b"\xff\xd8\xff")
            upload = {"path": image_path, "kind": "image", "root_path": directory}
            with mock.patch.object(ai_client, "extract_image_text_with_tesseract", return_value="Lease rent " * 20):
                with mock.patch.object(ai_client, "describe_image_for_search") as describe:
                    output_path = ai_client.prepare_upload_path_for_search(upload, directory)

            describe.assert_not_called()
            self.assertTrue(output_path.endswith(".image-ocr.txt"))
            with open(output_path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("OCR text for document-like image", text)
            self.assertIn("Lease rent", text)

    def test_non_document_image_gets_search_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "waldo.jpg")
            with open(image_path, "wb") as handle:
                handle.write(b"\xff\xd8\xff")
            upload = {"path": image_path, "kind": "image", "root_path": directory}
            with mock.patch.object(ai_client, "extract_image_text_with_tesseract", return_value=""):
                with mock.patch.object(ai_client, "describe_image_for_search", return_value="crowded scene with striped shirt"):
                    output_path = ai_client.prepare_upload_path_for_search(upload, directory)

            self.assertTrue(output_path.endswith(".image-description.txt"))
            with open(output_path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("Searchable image description", text)
            self.assertIn("crowded scene with striped shirt", text)

    def test_local_non_document_image_does_not_call_remote_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "waldo.jpg")
            with open(image_path, "wb") as handle:
                handle.write(b"\xff\xd8\xff")
            upload = {"path": image_path, "kind": "image", "root_path": directory}
            with mock.patch.object(ai_client, "extract_image_text_with_tesseract", return_value="") as ocr:
                with mock.patch.object(ai_client, "describe_image_for_search") as describe:
                    output_path = ai_client.prepare_upload_path_for_local_search(upload, directory)

            ocr.assert_called_once_with(image_path)
            describe.assert_not_called()
            self.assertTrue(output_path.endswith(".image-local.txt"))
            with open(output_path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("non-document image was not sent to OpenAI", text)

    def test_local_search_excludes_archive_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            active_dir = os.path.join(directory, "Active")
            old_dir = os.path.join(directory, "Old")
            os.makedirs(active_dir)
            os.makedirs(old_dir)
            active_path = os.path.join(active_dir, "lease.txt")
            old_path = os.path.join(old_dir, "lease.txt")
            with open(active_path, "w", encoding="utf-8") as handle:
                handle.write("Current tenant Alice pays rent 1000.")
            with open(old_path, "w", encoding="utf-8") as handle:
                handle.write("Former tenant Bob pays rent 9999.")

            db_path = os.path.join(directory, "local.sqlite3")
            index = LocalSearchIndex(db_path=db_path)
            try:
                for path in [active_path, old_path]:
                    upload = {"path": path, "kind": "text", "root_path": directory}
                    upload["classification"] = ai_client.classify_directory_path(path, directory)["classification"]
                    with open(path, encoding="utf-8") as handle:
                        text = handle.read()
                    index.upsert_upload(upload, ai_client.upload_metadata_header(upload) + text)
                current_results = index.search([directory], "create current rent roll rent")
                historical_results = index.search([directory], "create all historical rent roll rent")
            finally:
                index.close()

            current_text = "\n".join(result["text"] for result in current_results)
            historical_text = "\n".join(result["text"] for result in historical_results)
            self.assertIn("Alice", current_text)
            self.assertNotIn("Bob", current_text)
            self.assertIn("Bob", historical_text)

    def test_usage_summary_formats_labels_and_values_on_same_line(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=10_000,
                input_tokens_details=SimpleNamespace(cached_tokens=2_000),
                output_tokens=1_000,
                total_tokens=11_000,
                output_tokens_details=SimpleNamespace(reasoning_tokens=250),
            ),
            output=[
                SimpleNamespace(type="file_search_call", results=[{}, {}]),
                SimpleNamespace(type="web_search_call"),
            ],
        )

        line, detail = ai_client.build_usage_summary(
            response,
            0,
            [],
            [{"stats": {"reused": 3, "uploaded": 1, "failed": 0, "pruned": 0}, "remote_adopted": True}],
            {"stats": {"reused": 5, "indexed": 2, "failed": 1, "selected": 4}},
            {},
            ["sync"],
            model="gpt-5.5",
            prompt_cache_key="cligpt:test",
            prompt_cache_retention="24h",
        )

        lines = line.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("`usage_cost` input:10,000; output:1,000; reasoning:250; total:11,000; estimated_cost:$0.0835", lines[0])
        self.assertIn("`prompt_cache` cached_input:2,000; cache_hit:20.0%; prompt_cache_key:cligpt:test; prompt_cache_retention:24h", lines[1])
        self.assertIn("`file_search_direct_uploads` file_search:1 call(s), 2 result(s)", lines[2])
        self.assertIn("web_search:1 call(s)", lines[2])
        self.assertIn("direct_uploads:0 file(s), 0 B", lines[2])
        self.assertIn("local_tools:none", lines[2])
        self.assertIn("`directory` reused:3; uploaded:1; failed:0; pruned:0; remote_adopted:1; background_syncs:1", lines[3])
        self.assertIn("`local_search` reused:5; indexed:2; failed:1; selected:4", lines[4])
        self.assertAlmostEqual(detail["estimated_cost"]["cached_input"], 0.001)
        self.assertAlmostEqual(detail["estimated_cost"]["input"], 0.04)
        self.assertAlmostEqual(detail["estimated_cost"]["output"], 0.03)
        self.assertAlmostEqual(detail["estimated_cost"]["file_search"], 0.0025)
        self.assertAlmostEqual(detail["estimated_cost"]["web_search"], 0.01)
        self.assertEqual(detail["prompt_cache"]["cache_hit_ratio"], "20.0%")
        self.assertEqual(detail["stream_events"]["total"], 0)

    def test_usage_summary_unknown_cost_for_unpriced_model(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            ),
            output=[],
        )

        line, detail = ai_client.build_usage_summary(
            response,
            0,
            [],
            [],
            {},
            {},
            [],
            model="custom-model",
        )

        self.assertIn("estimated_cost:unknown", line)
        self.assertIsNone(detail["estimated_cost"])

    def test_cached_input_reduces_estimated_cost(self):
        no_cache = ai_client.estimate_response_cost(
            {
                "input_tokens": 10_000,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            },
            "gpt-5.5",
        )
        with_cache = ai_client.estimate_response_cost(
            {
                "input_tokens": 10_000,
                "cached_input_tokens": 8_000,
                "output_tokens": 0,
            },
            "gpt-5.5",
        )

        self.assertLess(with_cache["total"], no_cache["total"])
        self.assertAlmostEqual(no_cache["total"], 0.05)
        self.assertAlmostEqual(with_cache["input"], 0.01)
        self.assertAlmostEqual(with_cache["cached_input"], 0.004)
        self.assertAlmostEqual(with_cache["total"], 0.014)

    def test_prompt_cache_key_uses_stable_prefix_not_user_prompt(self):
        first = ai_client.build_prompt_cache_key(
            "gpt-5.5",
            "stable system",
            True,
            explicit_key=None,
        )
        second = ai_client.build_prompt_cache_key(
            "gpt-5.5",
            "stable system",
            True,
            explicit_key=None,
        )
        different_mode = ai_client.build_prompt_cache_key(
            "gpt-5.5",
            "stable system",
            False,
            explicit_key=None,
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, different_mode)
        self.assertTrue(first.startswith("cligpt:v2:gpt-5.5:web:none:"))

    def test_prompt_cache_retention_auto_uses_24h_for_gpt5_only(self):
        self.assertEqual(ai_client.normalize_prompt_cache_retention("auto", "gpt-5.5"), "24h")
        self.assertIsNone(ai_client.normalize_prompt_cache_retention("auto", "gpt-4o"))
        self.assertIsNone(ai_client.normalize_prompt_cache_retention("off", "gpt-5.5"))

    def test_user_prompt_is_last_content_item_for_cacheable_prefix(self):
        content = ai_client.build_user_content(
            "What is the answer?",
            [{"kind": "text", "path": "notes.txt", "text": "stable notes"}],
            local_context={"text": "stable local context"},
        )

        self.assertIn("stable local context", content[0]["text"])
        self.assertIn("stable notes", content[1]["text"])
        self.assertEqual(content[-1]["text"], "CURRENT USER QUESTION:\nWhat is the answer?")

    def test_compose_instructions_adds_cache_anchor_before_context(self):
        combined = ai_client.compose_instructions(
            "# Stable System\n\n# Pruned Context History",
            "Stable runtime.",
            "volatile conversation history",
            min_stable_words=80,
        )

        self.assertIn("# Prompt Cache Stability Anchor", combined)
        self.assertLess(
            combined.index("# Prompt Cache Stability Anchor"),
            combined.index("# Pruned Context History"),
        )
        self.assertTrue(combined.endswith("volatile conversation history"))

    def test_local_system_profile_tool_schema_and_output(self):
        schema = ai_client.local_tool_schemas()[0]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["name"], "get_system_profile")
        self.assertEqual(schema["parameters"]["additionalProperties"], False)

        output_item = ai_client.execute_local_tool_call({
            "name": "get_system_profile",
            "call_id": "call_123",
            "arguments": {},
        })

        self.assertEqual(output_item["type"], "function_call_output")
        self.assertEqual(output_item["call_id"], "call_123")
        payload = json.loads(output_item["output"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "get_system_profile")
        self.assertIn("os", payload["result"])
        self.assertIn("hardware", payload["result"])

    def test_render_stream_with_final_returns_combined_markdown(self):
        renderer = TerminalRenderer(RenderConfig(style="plain"))
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            result = renderer.render_stream_with_final(
                ["answer"],
                lambda streamed: streamed + "\n\n### Token Usage\n\n`usage_cost`: input:1",
            )

        self.assertEqual(stdout.getvalue(), "answer")
        self.assertEqual(result, "answer\n\n### Token Usage\n\n`usage_cost`: input:1")

    def test_debug_stream_event_logging_does_not_print_live_dots(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="hello"),
            SimpleNamespace(type="response.output_text.delta", delta=" world"),
            SimpleNamespace(type="response.output_text.delta", delta=" again"),
            SimpleNamespace(type="response.output_text.delta", delta=" and"),
            SimpleNamespace(type="response.output_text.delta", delta=" again"),
            SimpleNamespace(type="response.completed"),
        ]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            streamed = list(ai_client.iter_stream_with_heartbeat(events, debug=True))

        self.assertEqual([event.type for event in streamed], [event.type for event in events])
        output = stderr.getvalue()
        self.assertIn("[OpenAI stream opened; waiting for events]", output)
        self.assertNotIn("[OpenAI stream events]", output)
        self.assertNotIn("\n.", output)
        self.assertIn("[First visible output after", output)
        self.assertNotIn("[OpenAI stream event: response.output_text.delta]", output)
        self.assertNotIn("[OpenAI stream event: response.completed]", output)

    def test_stream_event_debug_line_formats_count_and_grouped_dots(self):
        self.assertEqual(
            ai_client.format_stream_event_debug_line(12),
            "[OpenAI Stream Events: 12] ..... ..... ..",
        )

    def test_request_input_debug_breakdown_includes_actual_payload_parts(self):
        request_args = {
            "instructions": "system text\n\nruntime text\n\ncontext text",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "local context"},
                        {"type": "input_file", "file_id": "file_123"},
                        {"type": "input_text", "text": "CURRENT USER QUESTION:\nhello"},
                    ],
                }
            ],
            "tools": [{"type": "web_search"}],
            "include": ["web_search_call.action.sources"],
            "reasoning": {"effort": "medium"},
            "text": {"format": {"type": "text"}},
        }

        breakdown = ai_client.request_input_debug_breakdown(
            request_args,
            system_message="system text",
            runtime_instructions="runtime text",
            pruned_context="context text",
            user_prompt="hello",
            local_context={"text": "local context"},
        )
        formatted = ai_client.format_request_input_debug_breakdown(breakdown)

        self.assertGreater(breakdown["estimated_request_input_tokens"], 0)
        self.assertEqual(breakdown["non_text_input_items"], 1)
        self.assertGreaterEqual(breakdown["tools_schema"], 1)
        self.assertGreaterEqual(breakdown["include_selectors"], 1)
        self.assertIn("[Request Input Estimate:", formatted)
        self.assertIn("[Tool Schemas:", formatted)
        self.assertIn("[Non-text Input Items: 1]", formatted)

    def test_raw_prompt_request_sends_only_prompt_payload(self):
        completed = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=3, output_tokens=1, total_tokens=4),
            output=[],
        )
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="ok"),
            SimpleNamespace(type="response.completed", response=completed),
        ]
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            return events

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(ai_client.client.responses, "create", side_effect=fake_create):
            with mock.patch.object(ai_client, "load_system_message") as load_system:
                with mock.patch.object(ai_client, "prune_context") as prune_context:
                    with mock.patch.object(ai_client, "extract_topic_tags") as tags:
                        with mock.patch.object(ai_client, "add_to_context"):
                            with contextlib.redirect_stdout(stdout):
                                with contextlib.redirect_stderr(stderr):
                                    answer = ai_client.single_query(
                                        "raw only",
                                        web_search=True,
                                        include_context=True,
                                        raw_prompt=True,
                                        output_style="plain",
                                    )

        self.assertEqual(answer.split("\n", 1)[0], "ok")
        load_system.assert_not_called()
        prune_context.assert_not_called()
        tags.assert_not_called()
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["input"], "raw only")
        self.assertNotIn("instructions", request)
        self.assertNotIn("tools", request)
        self.assertNotIn("include", request)
        self.assertNotIn("prompt_cache_key", request)
        self.assertNotIn("prompt_cache_retention", request)
        self.assertNotIn("text", request)
        self.assertNotIn("reasoning", request)

    def test_raw_prompt_rejects_attachments_and_directories(self):
        with self.assertRaisesRegex(ValueError, "--raw cannot be combined"):
            ai_client.single_query(
                "raw with file",
                file_paths=["notes.txt"],
                raw_prompt=True,
            )


if __name__ == "__main__":
    unittest.main()
