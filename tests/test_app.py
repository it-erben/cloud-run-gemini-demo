import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google import genai
from google.genai import errors, types
from google.oauth2.credentials import Credentials

import main


class WebTests(unittest.TestCase):
    def setUp(self):
        main.app.config.update(TESTING=True)
        self.client = main.app.test_client()

    def test_form_loads_without_cloud_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="prompt"', response.data)
        self.assertNotIn(b"Ergebnis speichern", response.data)

    def test_empty_prompt_is_rejected(self):
        response = self.client.post("/", data={"prompt": "   "})
        self.assertEqual(response.status_code, 400)

    def test_long_prompt_is_rejected(self):
        response = self.client.post("/", data={"prompt": "x" * 2001})
        self.assertEqual(response.status_code, 400)

    def test_answer_is_rendered_as_text(self):
        fake_client = unittest.mock.MagicMock()
        fake_client.models.generate_content.return_value = SimpleNamespace(
            text="<script>alert('x')</script>"
        )
        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch("main.genai.Client") as factory,
        ):
            factory.return_value.__enter__.return_value = fake_client
            response = self.client.post("/", data={"prompt": "Eine Frage"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>", response.data)

    def test_api_error_returns_gateway_error(self):
        fake_client = unittest.mock.MagicMock()
        fake_client.models.generate_content.side_effect = errors.ClientError(
            403, {"error": {"code": 403, "message": "internal error details"}}
        )
        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch("main.genai.Client") as factory,
        ):
            factory.return_value.__enter__.return_value = fake_client
            with self.assertLogs(main.app.logger, level="ERROR"):
                response = self.client.post("/", data={"prompt": "Eine Frage"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"internal error details", response.data)

    def test_empty_model_answer_is_explained(self):
        fake_client = unittest.mock.MagicMock()
        fake_client.models.generate_content.return_value = SimpleNamespace(text=None)
        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch("main.genai.Client") as factory,
        ):
            factory.return_value.__enter__.return_value = fake_client
            response = self.client.post("/", data={"prompt": "Eine Frage"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"keine Textantwort", response.data)

    def test_large_request_is_rejected(self):
        response = self.client.post("/", data={"prompt": "x" * 200000})
        self.assertEqual(response.status_code, 413)


class SDKTests(unittest.TestCase):
    def test_sdk_sends_prompt_to_vertex_endpoint(self):
        captured = []

        def respond(request):
            captured.append(request)
            if len(captured) > 1:
                return httpx.Response(
                    400,
                    json={"error": {"code": 400, "message": "No follow-up requests"}},
                )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"text": "Terraform beschreibt Infrastruktur."}
                                ],
                            },
                            "finishReason": "CONTINUATION",
                            "continuationToken": "dGVzdA==",
                        }
                    ]
                },
            )

        sdk_client = genai.Client(
            enterprise=True,
            project="test-project",
            location="global",
            credentials=Credentials(token="test-token"),
            http_options=types.HttpOptions(
                api_version="v1",
                client_args={"transport": httpx.MockTransport(respond)},
            ),
        )
        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_CLOUD_PROJECT": "test-project",
                    "BUCKET_NAME": "test-results",
                },
            ),
            patch("main.genai.Client", return_value=sdk_client),
            patch("google.cloud.storage.Client"),
        ):
            response = main.app.test_client().post(
                "/", data={"prompt": "Was ist Terraform?"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Terraform beschreibt Infrastruktur.", response.data)
        self.assertIn(b"Ergebnis speichern", response.data)
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual(request.url.host, "aiplatform.googleapis.com")
        self.assertIn("/projects/test-project/locations/global/", request.url.path)
        self.assertTrue(
            request.url.path.endswith("gemini-3.1-flash-lite:generateContent")
        )
        payload = json.loads(request.content)
        self.assertEqual(
            payload["contents"][0]["parts"][0]["text"], "Was ist Terraform?"
        )
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1024)


if __name__ == "__main__":
    unittest.main()
