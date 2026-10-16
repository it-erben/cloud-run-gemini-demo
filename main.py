import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from flask import Flask, render_template, request
from google import genai
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage
from google.genai import errors, types
from requests.exceptions import RequestException

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024


def render_result(prompt="", answer="", error="", saved_uri="", status=200):
    return render_template(
        "index.html",
        prompt=prompt,
        answer=answer,
        error=error,
        saved_uri=saved_uri,
        storage_enabled=bool(os.environ.get("BUCKET_NAME", "").strip()),
    ), status


@app.route("/", methods=["GET", "POST"])
def index():
    prompt = ""
    answer = ""
    error = ""
    status = 200

    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt or len(prompt) > 2000:
            error = "Gib eine Frage mit 1 bis 2000 Zeichen ein."
            status = 400
        else:
            try:
                project = os.environ.get("GOOGLE_CLOUD_PROJECT")
                if not project:
                    raise RuntimeError("GOOGLE_CLOUD_PROJECT fehlt.")
                with genai.Client(
                    enterprise=True,
                    project=project,
                    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                    http_options=types.HttpOptions(
                        api_version="v1",
                        timeout=60000,
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                ) as client:
                    response = client.models.generate_content(
                        model=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            max_output_tokens=1024,
                            automatic_continuation=False,
                        ),
                    )
                answer = response.text or "Gemini hat keine Textantwort geliefert."
            except (errors.APIError, GoogleAuthError, ValueError, RuntimeError):
                app.logger.exception("Gemini-Aufruf fehlgeschlagen")
                error = (
                    "Die Anfrage ist fehlgeschlagen. Details stehen in den Serverlogs."
                )
                status = 502

    return render_result(prompt=prompt, answer=answer, error=error, status=status)


@app.post("/save")
def save_result():
    prompt = request.form.get("prompt", "").strip()
    answer = request.form.get("answer", "")
    if not prompt or len(prompt) > 2000 or not answer.strip() or len(answer) > 20000:
        return render_result(
            prompt=prompt,
            answer=answer,
            error="Zum Speichern werden eine Frage und eine gültige Antwort benötigt.",
            status=400,
        )

    bucket_name = os.environ.get("BUCKET_NAME", "").strip()
    if not bucket_name:
        return render_result(
            prompt=prompt,
            answer=answer,
            error="Speichern ist gerade nicht verfügbar.",
            status=503,
        )

    created_at = datetime.now(UTC)
    object_name = f"answers/{created_at:%Y-%m-%d}/{uuid4().hex}.json"
    document = {
        "prompt": prompt,
        "answer": answer,
        "created_at": created_at.isoformat(),
    }
    try:
        with storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")) as client:
            blob = client.bucket(bucket_name).blob(object_name)
            blob.upload_from_string(
                json.dumps(document, ensure_ascii=False),
                content_type="application/json; charset=utf-8",
                if_generation_match=0,
                timeout=30,
                retry=None,
            )
    except (GoogleAPICallError, GoogleAuthError, RequestException, ValueError):
        app.logger.exception("Speichern in Cloud Storage fehlgeschlagen")
        return render_result(
            prompt=prompt,
            answer=answer,
            error="Speichern ist fehlgeschlagen. Die Antwort bleibt hier erhalten.",
            status=502,
        )

    return render_result(
        prompt=prompt,
        answer=answer,
        saved_uri=f"gs://{bucket_name}/{object_name}",
    )
