import os

from flask import Flask, render_template, request
from google import genai
from google.auth.exceptions import GoogleAuthError
from google.genai import errors, types

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


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

    return render_template(
        "index.html", prompt=prompt, answer=answer, error=error
    ), status
