# Cloud Run Gemini Demo

Python-Web-App mit Eingabefeld für einen Prompt und Textantwort von Gemini über
Vertex AI. Jede Anfrage startet ohne Chatverlauf. Die App nutzt Flask, Gunicorn
und das Google Gen AI SDK.

## Laufzeit

Der Container lauscht auf `0.0.0.0:$PORT`, standardmäßig Port `8080`.
Die HTML-Oberfläche liegt unter `/`; das Formular sendet einen POST an denselben
Pfad. Prompts sind auf 2000 Zeichen begrenzt, Antworten auf 1024 Ausgabetokens.

| Umgebungsvariable       | Bedeutung                              | Standardwert            |
| ----------------------- | -------------------------------------- | ----------------------- |
| `GOOGLE_CLOUD_PROJECT`  | Projekt-ID für Vertex AI, erforderlich | keiner                  |
| `GOOGLE_CLOUD_LOCATION` | Standort des Gemini-Endpunkts          | `global`                |
| `GEMINI_MODEL`          | Gemini-Modell                          | `gemini-3.1-flash-lite` |
| `PORT`                  | HTTP-Port des Containers               | `8080`                  |

Im Zielprojekt muss `aiplatform.googleapis.com` aktiviert sein. Der an Cloud Run
gebundene Service Account braucht `roles/aiplatform.user` auf diesem Projekt.
Das SDK bezieht seine Zugangsdaten über Application Default Credentials aus der
Cloud-Run-Laufzeit. API-Key und Service-Account-Schlüsseldatei entfallen.

`global` verwendet Googles globalen Endpunkt und garantiert keine Verarbeitung
in einer bestimmten Region. Der Cloud-Run-Standort und der Gemini-Endpunkt werden
getrennt konfiguriert.

Bei öffentlichem Zugriff kann jeder Besucher kostenpflichtige Gemini-Anfragen
auslösen. Cloud-Run-Instanzlimits setzen keine feste Grenze für Vertex-AI-Kosten.

## Image in Cloud Shell bauen

Das Docker-Repository in Artifact Registry muss bereits existieren. Der bauende
Benutzer braucht dort Schreibrechte, etwa `roles/artifactregistry.writer`.

```bash
git clone https://github.com/it-erben/cloud-run-gemini-demo.git
cd cloud-run-gemini-demo

export PROJECT_ID="DEINE-PROJEKT-ID"
export REGION="europe-west1"
export REPOSITORY="DEIN-ARTIFACT-REGISTRY-REPOSITORY"
export IMAGE_TAG="v1"
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/gemini-web:${IMAGE_TAG}"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker build --platform linux/amd64 --tag "$IMAGE" .
docker push "$IMAGE"
printf '%s\n' "$IMAGE"
```

Der ausgegebene Image-Pfad dient als Container-Image für Cloud Run.
Jeder neue Build bekommt einen neuen Tag, etwa `v2`.

## Lokal ausführen

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT="DEINE-PROJEKT-ID"
gunicorn --bind 0.0.0.0:8080 --timeout 90 main:app
```

Die Oberfläche ist unter <http://localhost:8080/> erreichbar. In Cloud Shell
übernimmt die bestehende Anmeldung den Zugriff. Außerhalb von Cloud Shell
richtet `gcloud auth application-default login` die lokalen Zugangsdaten ein.
Das lokale Konto braucht ebenfalls die Vertex-AI-Rolle.

## Prüfen

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
docker build --tag gemini-demo:local .
```

Die Tests prüfen Eingaben, HTML-Ausgabe, Fehlerfälle und die vom SDK erzeugte
Vertex-AI-Anfrage mit einem simulierten HTTP-Endpunkt. Sie benötigen keine
Cloud-Zugangsdaten und senden keine echten Gemini-Anfragen. GitHub Actions prüft
zusätzlich, ob der Container mit einem abweichenden `PORT` startet.

## Quellen

- [Cloud Run Service Identity](https://docs.cloud.google.com/run/docs/securing/service-identity)
- [Google Gen AI SDK](https://github.com/googleapis/python-genai)
- [Gemini-Modelle und Laufzeiten](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)
- [Images nach Artifact Registry pushen](https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling)
