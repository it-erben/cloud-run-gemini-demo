import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery, storage

import main


class FakeSession(AuthorizedSession):
    def __init__(self, reply):
        super().__init__(AnonymousCredentials())
        self.reply = reply
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.reply(url, kwargs)).encode()
        response.headers["content-type"] = "application/json"
        response.request = requests.Request(method, url).prepare()
        return response


class CloudSDKTests(unittest.TestCase):
    def test_bigquery_sdk_serializes_insert_and_reads_timestamp(self):
        def reply(url, kwargs):
            path = urlsplit(url).path
            if path.endswith("/insertAll"):
                return {}
            self.assertTrue(path.endswith("/queries"), url)
            return {
                "jobComplete": True,
                "jobReference": {
                    "projectId": "test-project",
                    "jobId": "test-job",
                    "location": "EU",
                },
                "schema": {
                    "fields": [
                        {"name": "prompt", "type": "STRING"},
                        {"name": "answer", "type": "STRING"},
                        {"name": "created_at", "type": "TIMESTAMP"},
                    ]
                },
                "totalRows": "1",
                "rows": [
                    {
                        "f": [
                            {"v": "Saved question"},
                            {"v": "Saved answer"},
                            {"v": "1792144800000000"},
                        ]
                    }
                ],
            }

        session = FakeSession(reply)
        sdk = bigquery.Client(
            project="test-project", credentials=AnonymousCredentials(), _http=session
        )
        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_CLOUD_PROJECT": "test-project",
                    "BQ_DATASET": "gemini_results",
                    "BQ_TABLE": "answers",
                },
                clear=True,
            ),
            patch("google.cloud.bigquery.Client", return_value=sdk),
        ):
            response = main.app.test_client().post(
                "/bigquery/save",
                data={"prompt": "Saved question", "answer": "Saved answer"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Saved question", response.data)
        self.assertIn(b"Saved answer", response.data)
        self.assertIn(b"2026-10-16", response.data)
        self.assertEqual(len(session.calls), 2)
        insert = json.loads(session.calls[0][2]["data"])
        self.assertEqual(insert["rows"][0]["json"]["prompt"], "Saved question")
        self.assertEqual(insert["rows"][0]["json"]["answer"], "Saved answer")
        self.assertRegex(insert["rows"][0]["insertId"], r"^[0-9a-f]{32}$")
        query = json.loads(session.calls[1][2]["data"])
        self.assertFalse(query["useLegacySql"])
        self.assertEqual(query["maximumBytesBilled"], "104857600")
        self.assertEqual(query["queryParameters"][0]["parameterValue"]["value"], "0")

    def test_gcs_sdk_keeps_next_page_token_after_page_size_limit(self):
        def reply(url, kwargs):
            return {
                "items": [
                    {
                        "name": f"file-{i}.txt",
                        "size": "123",
                        "updated": "2026-10-16T10:00:00Z",
                    }
                    for i in range(50)
                ],
                "nextPageToken": "page-two",
            }

        session = FakeSession(reply)
        sdk = storage.Client(
            project="test-project", credentials=AnonymousCredentials(), _http=session
        )
        with (
            patch.dict(
                os.environ,
                {"GOOGLE_CLOUD_PROJECT": "test-project", "BUCKET_NAME": "test-results"},
                clear=True,
            ),
            patch("google.cloud.storage.Client", return_value=sdk),
        ):
            response = main.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"file-49.txt", response.data)
        self.assertIn(b"/?page_token=page-two", response.data)
        object_requests = [
            call for call in session.calls if urlsplit(call[1]).path.endswith("/o")
        ]
        self.assertEqual(len(object_requests), 1)


if __name__ == "__main__":
    unittest.main()
