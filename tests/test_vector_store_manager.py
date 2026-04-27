import os
import tempfile
import unittest
from unittest import mock

import vector_store_manager


class FakeVectorStore:
    def __init__(self, vector_store_id, name, metadata):
        self.id = vector_store_id
        self.name = name
        self.metadata = metadata


class FakePage:
    def __init__(self, data):
        self.data = data
        self.has_more = False


class VectorStoreManagerTests(unittest.TestCase):
    def test_status_does_not_create_remote_corpus(self):
        with tempfile.TemporaryDirectory() as home:
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "note.txt")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("hello")

                fake_client = mock.Mock()
                manager = vector_store_manager.VectorStoreManager(openai_client=fake_client)
                upload = {"path": path, "kind": "text"}

                with mock.patch.dict(os.environ, {"GPT_HOME": home}):
                    status = manager.status_for_uploads(directory, [upload], create=False)

                self.assertFalse(status["complete"])
                self.assertEqual(status["counts"]["new"], 1)
                self.assertIsNone(status["vector_store_id"])
                fake_client.vector_stores.create.assert_not_called()

    def test_create_adopts_remote_vector_store_by_metadata(self):
        with tempfile.TemporaryDirectory() as home:
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "note.txt")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("hello")

                upload = {"path": path, "kind": "text"}
                fingerprint = vector_store_manager.directory_identity_fingerprint(directory, [upload])
                fake_client = mock.Mock()
                fake_client.vector_stores.list.return_value = FakePage([
                    FakeVectorStore(
                        "vs_existing",
                        "cligpt:existing",
                        {
                            "cligpt_schema_version": "1",
                            "cligpt_api_key_hash": "missing",
                            "cligpt_root_fingerprint": fingerprint,
                        },
                    )
                ])
                manager = vector_store_manager.VectorStoreManager(openai_client=fake_client)

                with mock.patch.dict(os.environ, {"GPT_HOME": home}, clear=True):
                    corpus = manager.ensure_corpus(directory, [upload])
                    status = manager.status_for_uploads(directory, [upload], create=False)

                self.assertEqual(corpus["vector_store_id"], "vs_existing")
                self.assertTrue(status["complete"])
                self.assertEqual(status["counts"]["completed"], 1)
                self.assertTrue(status["remote_adopted"])
                fake_client.vector_stores.create.assert_not_called()

    def test_create_makes_remote_vector_store_with_portable_metadata(self):
        with tempfile.TemporaryDirectory() as home:
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "note.txt")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("hello")

                upload = {"path": path, "kind": "text"}
                fake_client = mock.Mock()
                fake_client.vector_stores.list.return_value = FakePage([])
                fake_client.vector_stores.create.return_value = FakeVectorStore("vs_new", "new", {})
                manager = vector_store_manager.VectorStoreManager(openai_client=fake_client)

                with mock.patch.dict(os.environ, {"GPT_HOME": home, "OPENAI_API_KEY": "sk-test"}):
                    corpus = manager.ensure_corpus(directory, [upload])

                self.assertEqual(corpus["vector_store_id"], "vs_new")
                create_kwargs = fake_client.vector_stores.create.call_args.kwargs
                self.assertIn("cligpt_root_fingerprint", create_kwargs["metadata"])
                self.assertEqual(create_kwargs["metadata"]["cligpt_schema_version"], "1")
                self.assertNotEqual(create_kwargs["metadata"]["cligpt_api_key_hash"], "sk-test")


if __name__ == "__main__":
    unittest.main()
