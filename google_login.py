import hmac
import os
import secrets
import time
from datetime import timedelta
from functools import partial

import requests
from flask import abort, jsonify, redirect, render_template, request, session
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from requests.exceptions import RequestException

SESSION_SECONDS = 3600


def verify_google_token(credential, audience):
    with requests.Session() as http:
        transport = partial(Request(session=http), timeout=10)
        return id_token.verify_oauth2_token(credential, transport, audience=audience)


def same_token(expected, actual):
    return (
        isinstance(expected, str)
        and isinstance(actual, str)
        and bool(expected)
        and hmac.compare_digest(expected.encode(), actual.encode())
    )


def valid_csrf():
    return same_token(
        session.get("csrf_token"),
        request.headers.get("X-CSRF-Token") or request.form.get("csrf_token"),
    )


def init_auth(app):
    app.config.update(
        SECRET_KEY=os.environ.get("SESSION_SECRET", ""),
        GOOGLE_OAUTH_CLIENT_ID=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_SECONDS),
    )

    def configured():
        secret = app.config.get("SECRET_KEY")
        return (
            isinstance(secret, str)
            and len(secret) >= 32
            and bool(app.config.get("GOOGLE_OAUTH_CLIENT_ID"))
        )

    @app.before_request
    def require_login():
        if request.endpoint in (None, "static", "healthz"):
            return None
        if not configured():
            if request.endpoint == "index" and request.method in ("GET", "HEAD"):
                return render_template("login.html", login_enabled=False)
            abort(503, description="Die Anmeldung ist noch nicht eingerichtet.")
        if request.endpoint == "login_callback":
            return None
        if session.get("user") and session.get("expires_at", 0) <= time.time():
            session.clear()
        if not session.get("user"):
            if request.endpoint == "index" and request.method in ("GET", "HEAD"):
                session.setdefault("csrf_token", secrets.token_urlsafe(32))
                session.setdefault("login_nonce", secrets.token_urlsafe(32))
                session["return_path"] = request.path
                return render_template(
                    "login.html",
                    login_enabled=True,
                    google_oauth_client_id=app.config["GOOGLE_OAUTH_CLIENT_ID"],
                    login_nonce=session["login_nonce"],
                )
            abort(401, description="Bitte zuerst mit Google anmelden.")
        if request.method not in ("GET", "HEAD", "OPTIONS") and not valid_csrf():
            abort(403, description="Das Formular ist abgelaufen. Bitte neu laden.")
        return None

    @app.context_processor
    def auth_context():
        return {
            "csrf_token": session.get("csrf_token", ""),
            "user": session.get("user"),
        }

    @app.after_request
    def private_response(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/auth/callback")
    def login_callback():
        if not valid_csrf():
            abort(403, description="Die Anmeldung ist abgelaufen. Bitte neu laden.")
        payload = request.get_json(silent=True)
        credential = payload.get("credential") if isinstance(payload, dict) else None
        if not isinstance(credential, str) or not 1 <= len(credential) <= 8192:
            abort(400)
        try:
            claims = verify_google_token(
                credential, app.config["GOOGLE_OAUTH_CLIENT_ID"]
            )
        except (TransportError, RequestException):
            return jsonify(
                error="Google ist gerade nicht erreichbar. Bitte erneut versuchen."
            ), 503
        except (ValueError, GoogleAuthError):
            return jsonify(
                error="Die Google-Anmeldung konnte nicht geprüft werden."
            ), 401
        if (
            not same_token(session.get("login_nonce"), claims.get("nonce"))
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
        ):
            return jsonify(
                error="Die Google-Anmeldung ist abgelaufen. Bitte neu laden."
            ), 401
        return_path = session.get("return_path", "/")
        if return_path not in ("/", "/bigquery"):
            return_path = "/"
        session.clear()
        session.permanent = True
        session["user"] = {
            key: claims[key][:256]
            for key in ("sub", "email", "name")
            if isinstance(claims.get(key), str)
        }
        session["csrf_token"] = secrets.token_urlsafe(32)
        session["expires_at"] = time.time() + SESSION_SECONDS
        return jsonify(redirect=return_path)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect("/")
