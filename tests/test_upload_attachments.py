import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import ai_client
from local_search import LocalSearchIndex


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


if __name__ == "__main__":
    unittest.main()
