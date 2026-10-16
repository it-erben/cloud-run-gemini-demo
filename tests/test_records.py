import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from auth_helpers import signed_in_client
from google.api_core.exceptions import Forbidden

import main


class RecordViewsTests(unittest.TestCase):
    def setUp(self):
        main.app.config.update(TESTING=True)
        self.client = signed_in_client()
        self.env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "BUCKET_NAME": "test-results",
            "BQ_DATASET": "gemini_results",
            "BQ_TABLE": "answers",
        }
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.gcs_factory = patch("google.cloud.storage.Client").start()
        self.bq_factory = patch("google.cloud.bigquery.Client", create=True).start()
        self.addCleanup(patch.stopall)
        self.gcs = self.gcs_factory.return_value.__enter__.return_value
        self.bq = self.bq_factory.return_value.__enter__.return_value
        self.gcs.list_blobs.return_value.pages = iter([[]])
        self.gcs.list_blobs.return_value.next_page_token = None
        self.bq.query_and_wait.return_value = []
        self.bq.insert_rows_json.return_value = []
        self.result = {"prompt": "Eine Frage", "answer": "Eine Antwort mit ÄÖÜ"}

    def test_gcs_lists_all_object_types_with_metadata_and_next_page(self):
        blobs = self.gcs.list_blobs.return_value
        blobs.pages = iter(
            [
                [
                    SimpleNamespace(
                        name="notes/<script>.txt",
                        size=123,
                        updated=datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
                    )
                ]
            ]
        )
        blobs.next_page_token = "next/+token="
        response = self.client.get("/?page_token=first-token")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"notes/&lt;script&gt;.txt", response.data)
        self.assertNotIn(b"<script>", response.data)
        self.assertIn(b"123", response.data)
        self.assertIn(b"next/%2Btoken%3D", response.data)
        call = self.gcs.list_blobs.call_args
        self.assertEqual(call.args, ("test-results",))
        self.assertEqual(call.kwargs["page_token"], "first-token")
        self.assertEqual(call.kwargs["max_results"], 50)
        self.assertNotIn("prefix", call.kwargs)
        self.bq_factory.assert_not_called()

    def test_gcs_empty_list_and_refresh_are_visible(self):
        response = self.client.get("/")
        self.assertIn(b"Noch keine Dateien", response.data)
        self.assertIn(b"Aktualisieren", response.data)
        self.assertIn(b'href="/bigquery"', response.data)

    def test_listing_failure_does_not_hide_generated_answer(self):
        self.gcs.list_blobs.side_effect = Forbidden("private details")
        with (
            patch("main.genai.Client") as factory,
            self.assertLogs(main.app.logger, level="ERROR"),
        ):
            factory.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(
                text="Die Antwort"
            )
            response = self.client.post("/", data={"prompt": "Die Frage"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Die Antwort", response.data)
        self.assertIn(b"Liste konnte nicht geladen werden", response.data)
        self.assertNotIn(b"private details", response.data)
        self.assertIn(b"Ergebnis speichern", response.data)

    def test_bigquery_tab_queries_and_renders_rows_as_text(self):
        self.bq.query_and_wait.return_value = [
            {
                "prompt": "<script>bad()</script>",
                "answer": "<img src=x onerror=bad()>",
                "created_at": datetime(2026, 10, 16, tzinfo=UTC),
            }
        ]
        response = self.client.get("/bigquery")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>", response.data)
        self.assertIn(b"&lt;img", response.data)
        self.assertIn(b'action="/bigquery"', response.data)
        self.assertIn(b"test-project.gemini_results.answers", response.data)
        call = self.bq.query_and_wait.call_args
        self.assertIn("`test-project.gemini_results.answers`", call.args[0])
        self.assertIn("ORDER BY created_at DESC", call.args[0])
        self.assertEqual(call.kwargs["max_results"], 51)
        self.assertEqual(
            call.kwargs["job_config"].maximum_bytes_billed, 100 * 1024 * 1024
        )
        self.gcs_factory.assert_not_called()

    def test_bigquery_pagination_keeps_extra_row_for_next_page(self):
        self.bq.query_and_wait.return_value = [
            {"prompt": f"question-{i}", "answer": "answer", "created_at": None}
            for i in range(51)
        ]
        response = self.client.get("/bigquery?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"question-49", response.data)
        self.assertNotIn(b"question-50", response.data)
        self.assertIn(b"/bigquery?page=3", response.data)
        self.assertIn(b"/bigquery?page=1", response.data)
        config = self.bq.query_and_wait.call_args.kwargs["job_config"]
        self.assertEqual(config.query_parameters[0].value, 100)

    def test_bigquery_missing_configuration_does_not_access_cloud(self):
        for missing in ("GOOGLE_CLOUD_PROJECT", "BQ_DATASET", "BQ_TABLE"):
            with self.subTest(missing=missing), patch.dict(os.environ, {missing: ""}):
                response = self.client.get("/bigquery")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"BQ_DATASET", response.data)
                self.assertIn(b"BQ_TABLE", response.data)
        self.bq_factory.assert_not_called()
        self.gcs_factory.assert_not_called()

    def test_bigquery_invalid_page_is_rejected_without_query(self):
        for page in ("-1", "abc", "1 OR 1=1"):
            with self.subTest(page=page):
                response = self.client.get("/bigquery", query_string={"page": page})
                self.assertEqual(response.status_code, 400)
        self.bq_factory.assert_not_called()

    def test_bigquery_configuration_cannot_inject_sql(self):
        with (
            patch.dict(os.environ, {"BQ_TABLE": "answers` UNION SELECT 1--"}),
            self.assertLogs(main.app.logger, level="ERROR"),
        ):
            response = self.client.get("/bigquery")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Liste konnte nicht geladen werden", response.data)
        self.bq_factory.assert_not_called()

    def test_bigquery_generates_answer_and_uses_its_own_save_form(self):
        with patch("main.genai.Client") as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(
                text="BQ answer"
            )
            response = self.client.post("/bigquery", data={"prompt": "Question"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BQ answer", response.data)
        self.assertIn(b'action="/bigquery/save"', response.data)
        self.bq.insert_rows_json.assert_not_called()

    def test_bigquery_save_writes_one_row_and_refreshes_list(self):
        with patch(
            "main.genai.Client", side_effect=AssertionError("Unexpected model call")
        ):
            response = self.client.post("/bigquery/save", data=self.result)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Gespeichert", response.data)
        call = self.bq.insert_rows_json.call_args
        self.assertEqual(call.args[0], "test-project.gemini_results.answers")
        row = call.args[1][0]
        self.assertEqual(row["prompt"], self.result["prompt"])
        self.assertEqual(row["answer"], self.result["answer"])
        self.assertIsNotNone(datetime.fromisoformat(row["created_at"]).tzinfo)
        self.assertEqual(len(call.kwargs["row_ids"]), 1)
        self.assertIsNone(call.kwargs["retry"])
        self.bq.query_and_wait.assert_called_once()
        self.gcs_factory.assert_not_called()

    def test_bigquery_row_errors_keep_answer_and_allow_retry(self):
        self.bq.insert_rows_json.return_value = [
            {"index": 0, "errors": [{"message": "private schema details"}]}
        ]
        with self.assertLogs(main.app.logger, level="ERROR"):
            response = self.client.post("/bigquery/save", data=self.result)
        self.assertEqual(response.status_code, 502)
        self.assertIn(self.result["answer"].encode(), response.data)
        self.assertNotIn(b"private schema details", response.data)
        self.assertIn(b'action="/bigquery/save"', response.data)
        self.assertNotIn(b"Gespeichert", response.data)

    def test_bigquery_permission_error_does_not_report_success(self):
        self.bq.insert_rows_json.side_effect = Forbidden("private details")
        with self.assertLogs(main.app.logger, level="ERROR"):
            response = self.client.post("/bigquery/save", data=self.result)
        self.assertEqual(response.status_code, 502)
        self.assertIn(self.result["answer"].encode(), response.data)
        self.assertNotIn(b"private details", response.data)

    def test_bigquery_save_requires_configuration_and_valid_result(self):
        with patch.dict(os.environ, {"BQ_TABLE": ""}):
            response = self.client.post("/bigquery/save", data=self.result)
            self.assertEqual(response.status_code, 503)
        for data in (
            {"prompt": "", "answer": "answer"},
            {"prompt": "p", "answer": " "},
            {"prompt": "p", "answer": "x" * 20001},
        ):
            with self.subTest(data_length=sum(map(len, data.values()))):
                response = self.client.post("/bigquery/save", data=data)
                self.assertEqual(response.status_code, 400)
        self.bq.insert_rows_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
