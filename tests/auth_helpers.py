import time

from flask.testing import FlaskClient

import main


class SignedInClient(FlaskClient):
    def post(self, *args, **kwargs):
        kwargs["data"] = {"csrf_token": "test-csrf", **kwargs.get("data", {})}
        return super().post(*args, **kwargs)


def signed_in_client():
    main.app.config.update(
        TESTING=True,
        SECRET_KEY="test-session-secret-with-at-least-32-characters",
        GOOGLE_OAUTH_CLIENT_ID="demo.apps.googleusercontent.com",
    )
    client = SignedInClient(main.app, main.app.response_class, use_cookies=True)
    with client.session_transaction() as session:
        session["user"] = {"sub": "test-user", "name": "Ada"}
        session["csrf_token"] = "test-csrf"
        session["expires_at"] = time.time() + 3600
    return client
