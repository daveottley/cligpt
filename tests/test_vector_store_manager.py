import os
import tempfile
import unittest
from unittest import mock

import vector_store_manager


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


if __name__ == "__main__":
    unittest.main()
