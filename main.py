import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from flask import Flask, abort, render_template, request, url_for
from google import genai
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage
from google.genai import errors, types
from requests.exceptions import RequestException

from cloud_records import (
    bigquery_table,
    list_bigquery_rows,
    list_objects,
    save_bigquery_row,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024


CLOUD_ERRORS = (
    GoogleAPICallError,
    GoogleAuthError,
    RequestException,
    ValueError,
    TimeoutError,
)


def render_result(
    prompt="", answer="", error="", saved_uri="", status=200, backend="gcs"
):
    bucket_name = os.environ.get("BUCKET_NAME", "").strip()
    bq_enabled = all(
        os.environ.get(name, "").strip()
        for name in ("GOOGLE_CLOUD_PROJECT", "BQ_DATASET", "BQ_TABLE")
    )
    enabled = bq_enabled if backend == "bigquery" else bool(bucket_name)
    items = []
    listing_error = ""
    destination = bucket_name
    next_url = ""
    previous_url = ""
    page = 0
    if backend == "bigquery" and request.method == "GET":
        try:
            page = int(request.args.get("page", "0"))
            if page < 0 or page > 1000000:
                abort(400)
        except ValueError:
            abort(400)
    if enabled:
        try:
            if backend == "bigquery":
                destination = bigquery_table()
                items, has_more = list_bigquery_rows(page)
                if has_more:
                    next_url = url_for("index", backend=backend, page=page + 1)
                if page:
                    previous_url = url_for("index", backend=backend, page=page - 1)
            else:
                token = (
                    request.args.get("page_token") if request.method == "GET" else None
                )
                items, next_token = list_objects(bucket_name, token)
                if next_token:
                    next_url = url_for("index", backend=backend, page_token=next_token)
        except CLOUD_ERRORS:
            app.logger.exception("Liste aus %s konnte nicht geladen werden", backend)
            listing_error = "Die Liste konnte nicht geladen werden. Details stehen in den Serverlogs."
    return render_template(
        "index.html",
        prompt=prompt,
        answer=answer,
        error=error,
        saved_uri=saved_uri,
        storage_enabled=enabled,
        backend=backend,
        items=items,
        listing_error=listing_error,
        destination=destination,
        next_url=next_url,
        previous_url=previous_url,
        page=page,
    ), status


@app.route("/", methods=["GET", "POST"], defaults={"backend": "gcs"})
@app.route("/bigquery", methods=["GET", "POST"], defaults={"backend": "bigquery"})
def index(backend):
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

    return render_result(
        prompt=prompt, answer=answer, error=error, status=status, backend=backend
    )


@app.post("/save", defaults={"backend": "gcs"})
@app.post("/bigquery/save", defaults={"backend": "bigquery"})
def save_result(backend):
    prompt = request.form.get("prompt", "").strip()
    answer = request.form.get("answer", "")
    if not prompt or len(prompt) > 2000 or not answer.strip() or len(answer) > 20000:
        return render_result(
            backend=backend,
            prompt=prompt,
            answer=answer,
            error="Zum Speichern werden eine Frage und eine gültige Antwort benötigt.",
            status=400,
        )

    bucket_name = os.environ.get("BUCKET_NAME", "").strip()
    configured = (
        bool(bucket_name)
        if backend == "gcs"
        else all(
            os.environ.get(name, "").strip()
            for name in ("GOOGLE_CLOUD_PROJECT", "BQ_DATASET", "BQ_TABLE")
        )
    )
    if not configured:
        return render_result(
            backend=backend,
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
        if backend == "bigquery":
            saved_uri = save_bigquery_row(document)
        else:
            with storage.Client(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT")
            ) as client:
                blob = client.bucket(bucket_name).blob(object_name)
                blob.upload_from_string(
                    json.dumps(document, ensure_ascii=False),
                    content_type="application/json; charset=utf-8",
                    if_generation_match=0,
                    timeout=30,
                    retry=None,
                )
            saved_uri = f"gs://{bucket_name}/{object_name}"
    except CLOUD_ERRORS:
        app.logger.exception("Speichern in %s fehlgeschlagen", backend)
        return render_result(
            backend=backend,
            prompt=prompt,
            answer=answer,
            error="Speichern ist fehlgeschlagen. Die Antwort bleibt hier erhalten.",
            status=502,
        )

    return render_result(
        prompt=prompt,
        answer=answer,
        saved_uri=saved_uri,
        backend=backend,
    )
