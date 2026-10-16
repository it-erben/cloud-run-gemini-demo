import time
import unittest
from unittest.mock import patch

from google.auth.exceptions import TransportError

import main

CLIENT_ID = "demo.apps.googleusercontent.com"
SECRET = "test-session-secret-with-at-least-32-characters"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.old_config = main.app.config.copy()
        main.app.config.update(
            TESTING=True, SECRET_KEY=SECRET, GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID
        )
        self.addCleanup(lambda: main.app.config.update(self.old_config))
        self.client = main.app.test_client()

    def prepare_login(self, path="/"):
        self.client.get(path)
        with self.client.session_transaction() as session:
            return session["csrf_token"], session["login_nonce"]

    def login(self, claims=None, path="/"):
        csrf, nonce = self.prepare_login(path)
        claims = claims or {"sub": "google-user-123", "nonce": nonce, "name": "Ada"}
        with patch("google_login.verify_google_token", return_value=claims):
            return self.client.post(
                "/auth/callback",
                json={"credential": "signed-google-token"},
                headers={"X-CSRF-Token": csrf},
            )

    def test_anonymous_pages_do_not_access_cloud_data(self):
        with (
            patch("main.list_objects") as objects,
            patch("main.list_bigquery_rows") as rows,
            patch("main.genai.Client") as gemini,
        ):
            for path in ("/", "/bigquery"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"g_id_signin", response.data)
                self.assertNotIn(b'name="prompt"', response.data)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            objects.assert_not_called()
            rows.assert_not_called()
            gemini.assert_not_called()

    def test_anonymous_posts_are_rejected(self):
        for path in ("/", "/bigquery", "/save", "/bigquery/save", "/logout"):
            self.assertEqual(self.client.post(path).status_code, 401)

    def test_missing_configuration_fails_closed_and_health_still_works(self):
        for config in ({"GOOGLE_OAUTH_CLIENT_ID": ""}, {"SECRET_KEY": "short"}):
            with self.subTest(config=config):
                main.app.config.update(
                    SECRET_KEY=SECRET, GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID
                )
                main.app.config.update(config)
                response = self.client.get("/")
                self.assertIn(b"Anmeldung ist noch nicht eingerichtet", response.data)
                self.assertNotIn(b"g_id_signin", response.data)
                self.assertEqual(self.client.post("/").status_code, 503)
                self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_success_rotates_session_and_returns_to_selected_tab(self):
        response = self.login(path="/bigquery")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"redirect": "/bigquery"})
        with self.client.session_transaction() as session:
            self.assertEqual(session["user"]["sub"], "google-user-123")
            self.assertNotIn("login_nonce", session)
            self.assertIn("csrf_token", session)
        cookie = response.headers["Set-Cookie"]
        for attribute in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(attribute, cookie)

    def test_callback_requires_csrf_before_verifying_token(self):
        self.prepare_login()
        with patch("google_login.verify_google_token") as verify:
            for token in ("", "wrong", "ü"):
                response = self.client.post(
                    "/auth/callback",
                    json={"credential": "token"},
                    headers={"X-CSRF-Token": token},
                )
                self.assertEqual(response.status_code, 403)
            verify.assert_not_called()

    def test_callback_requires_session_nonce(self):
        response = self.login({"sub": "user", "nonce": "wrong"})
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)

    def test_login_nonce_survives_loading_another_tab(self):
        first_csrf, first_nonce = self.prepare_login()
        csrf, nonce = self.prepare_login("/bigquery")
        self.assertEqual((csrf, nonce), (first_csrf, first_nonce))

    def test_google_network_failure_allows_retry_without_login(self):
        csrf, nonce = self.prepare_login()
        with patch("google_login.verify_google_token", side_effect=TransportError()):
            response = self.client.post(
                "/auth/callback",
                json={"credential": "token"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 503)
        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)
            self.assertEqual(session["login_nonce"], nonce)

    def test_tampered_session_cookie_cannot_access_app(self):
        self.login()
        name = main.app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(name).value
        self.client.set_cookie(name, "tampered" + cookie)
        self.assertIn(b"g_id_signin", self.client.get("/").data)

    def test_callback_rejects_missing_subject(self):
        csrf, nonce = self.prepare_login()
        with patch("google_login.verify_google_token", return_value={"nonce": nonce}):
            response = self.client.post(
                "/auth/callback",
                json={"credential": "token"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 401)

    def test_callback_rejects_malformed_credentials(self):
        csrf, _ = self.prepare_login()
        with patch("google_login.verify_google_token") as verify:
            for payload in ([], {}, {"credential": 123}, {"credential": "x" * 8193}):
                response = self.client.post(
                    "/auth/callback", json=payload, headers={"X-CSRF-Token": csrf}
                )
                self.assertEqual(response.status_code, 400)
            verify.assert_not_called()

    def test_invalid_token_is_not_exposed(self):
        csrf, _ = self.prepare_login()
        with patch(
            "google_login.verify_google_token", side_effect=ValueError("private")
        ):
            response = self.client.post(
                "/auth/callback",
                json={"credential": "private-token"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b"private", response.data)

    def test_authenticated_posts_require_csrf(self):
        self.login()
        for path in ("/", "/bigquery", "/save", "/bigquery/save", "/logout"):
            self.assertEqual(self.client.post(path).status_code, 403)
        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        self.assertEqual(
            self.client.post("/", data={"csrf_token": csrf, "prompt": ""}).status_code,
            400,
        )

    def test_logout_clears_session(self):
        self.login()
        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(
            self.client.post("/logout", data={"csrf_token": csrf}).status_code, 302
        )
        self.assertIn(b"g_id_signin", self.client.get("/").data)

    def test_expired_session_cannot_access_app(self):
        self.login()
        with self.client.session_transaction() as session:
            session["expires_at"] = time.time() - 1
        self.assertIn(b"g_id_signin", self.client.get("/").data)
        self.assertEqual(self.client.post("/save").status_code, 401)

    def test_profile_is_escaped_and_external_redirect_ignored(self):
        csrf, nonce = self.prepare_login()
        with self.client.session_transaction() as session:
            session["return_path"] = "https://evil.example"
        with patch(
            "google_login.verify_google_token",
            return_value={
                "sub": "user",
                "nonce": nonce,
                "name": "<script>bad()</script>",
            },
        ):
            response = self.client.post(
                "/auth/callback",
                json={"credential": "token"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.json, {"redirect": "/"})
        with patch.dict("os.environ", {}, clear=True):
            page = self.client.get("/")
        self.assertIn(b"&lt;script&gt;", page.data)
        self.assertNotIn(b"<script>bad()", page.data)
