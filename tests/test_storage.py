import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import Forbidden
from requests.exceptions import Timeout

import main


class StorageTests(unittest.TestCase):
    def setUp(self):
        main.app.config.update(TESTING=True)
        self.client = main.app.test_client()
        self.env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "BUCKET_NAME": "test-results",
        }
        self.result = {
            "prompt": "Was ist Terraform?",
            "answer": "Infrastruktur als Code. ÄÖÜ",
        }

    def test_save_writes_result_as_json_without_another_model_call(self):
        storage_client = MagicMock()
        blob = storage_client.bucket.return_value.blob.return_value
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("google.cloud.storage.Client") as factory,
            patch(
                "main.genai.Client", side_effect=AssertionError("Unexpected model call")
            ),
        ):
            factory.return_value.__enter__.return_value = storage_client
            response = self.client.post("/save", data=self.result)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"gs://test-results/answers/", response.data)
        self.assertIn("Infrastruktur als Code. ÄÖÜ".encode(), response.data)
        storage_client.bucket.assert_called_once_with("test-results")
        object_name = storage_client.bucket.return_value.blob.call_args.args[0]
        self.assertRegex(object_name, r"^answers/\d{4}-\d{2}-\d{2}/[0-9a-f]{32}\.json$")
        upload = blob.upload_from_string.call_args
        document = json.loads(upload.args[0])
        self.assertEqual(document["prompt"], "Was ist Terraform?")
        self.assertEqual(document["answer"], "Infrastruktur als Code. ÄÖÜ")
        self.assertIsNotNone(datetime.fromisoformat(document["created_at"]).tzinfo)
        self.assertEqual(
            upload.kwargs["content_type"], "application/json; charset=utf-8"
        )
        self.assertEqual(upload.kwargs["if_generation_match"], 0)

    def test_missing_bucket_does_not_upload(self):
        with (
            patch.dict(
                os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True
            ),
            patch(
                "google.cloud.storage.Client",
                side_effect=AssertionError("Unexpected storage call"),
            ),
        ):
            response = self.client.post("/save", data=self.result)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Infrastruktur als Code. ÄÖÜ".encode(), response.data)

    def test_invalid_result_does_not_upload(self):
        for data in (
            {"prompt": "", "answer": "Eine Antwort"},
            {"prompt": "Eine Frage", "answer": ""},
            {"prompt": "x" * 2001, "answer": "Eine Antwort"},
            {"prompt": "Eine Frage", "answer": "x" * 20001},
        ):
            with (
                self.subTest(data_length=sum(map(len, data.values()))),
                patch.dict(os.environ, self.env, clear=True),
                patch("google.cloud.storage.Client") as factory,
            ):
                response = self.client.post("/save", data=data)
                self.assertEqual(response.status_code, 400)
                factory.return_value.__enter__.return_value.bucket.return_value.blob.return_value.upload_from_string.assert_not_called()

    def test_upload_error_keeps_answer_and_hides_server_details(self):
        storage_client = MagicMock()
        storage_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = Forbidden(
            "private server details"
        )
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("google.cloud.storage.Client") as factory,
            self.assertLogs(main.app.logger, level="ERROR"),
        ):
            factory.return_value.__enter__.return_value = storage_client
            response = self.client.post("/save", data=self.result)
        self.assertEqual(response.status_code, 502)
        self.assertIn("Infrastruktur als Code. ÄÖÜ".encode(), response.data)
        self.assertNotIn(b"private server details", response.data)
        self.assertIn(b"Ergebnis speichern", response.data)

    def test_save_form_escapes_browser_supplied_text(self):
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("google.cloud.storage.Client") as factory,
        ):
            response = self.client.post(
                "/save",
                data={
                    "prompt": "<script>bad()</script>",
                    "answer": "<img src=x onerror=bad()>",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>", response.data)
        self.assertNotIn(b"<img src=x", response.data)
        factory.return_value.__enter__.return_value.bucket.assert_called_once_with(
            "test-results"
        )

    def test_upload_timeout_keeps_answer_available_for_retry(self):
        storage_client = MagicMock()
        storage_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = Timeout(
            "Upload timed out"
        )
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("google.cloud.storage.Client") as factory,
            self.assertLogs(main.app.logger, level="ERROR"),
        ):
            factory.return_value.__enter__.return_value = storage_client
            response = self.client.post("/save", data=self.result)
        self.assertEqual(response.status_code, 502)
        self.assertIn("Infrastruktur als Code. ÄÖÜ".encode(), response.data)
        self.assertIn(b"Ergebnis speichern", response.data)


if __name__ == "__main__":
    unittest.main()
