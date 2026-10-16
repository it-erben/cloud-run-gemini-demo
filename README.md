# Cloud Run Gemini Demo

Python-Web-App mit Eingabefeld für einen Prompt und Textantwort von Gemini über
Vertex AI. Antworten lassen sich über einen Button in Cloud Storage speichern.
Jede Anfrage startet ohne Chatverlauf. Die App nutzt Flask, Gunicorn und die
Google-Clients für Gemini und Cloud Storage.

## Laufzeit

Der Container lauscht auf `0.0.0.0:$PORT`, standardmäßig Port `8080`.
Die HTML-Oberfläche liegt unter `/`; das Formular sendet einen POST an denselben
Pfad. Prompts sind auf 2000 Zeichen begrenzt, Antworten auf 1024 Ausgabetokens.

| Umgebungsvariable       | Bedeutung                              | Standardwert            |
| ----------------------- | -------------------------------------- | ----------------------- |
| `GOOGLE_CLOUD_PROJECT`  | Projekt-ID für Vertex AI, erforderlich | keiner                  |
| `GOOGLE_CLOUD_LOCATION` | Standort des Gemini-Endpunkts          | `global`                |
| `GEMINI_MODEL`          | Gemini-Modell                          | `gemini-3.1-flash-lite` |
| `BUCKET_NAME`           | Name des vorhandenen Ergebnis-Buckets  | keiner                  |
| `PORT`                  | HTTP-Port des Containers               | `8080`                  |

Im Zielprojekt muss `aiplatform.googleapis.com` aktiviert sein. Der an Cloud Run
gebundene Service Account braucht `roles/aiplatform.user` auf diesem Projekt.
Das SDK bezieht seine Zugangsdaten über Application Default Credentials aus der
Cloud-Run-Laufzeit. API-Key und Service-Account-Schlüsseldatei entfallen.

`global` verwendet Googles globalen Endpunkt und garantiert keine Verarbeitung
in einer bestimmten Region. Der Cloud-Run-Standort und der Gemini-Endpunkt werden
getrennt konfiguriert.

Bei öffentlichem Zugriff kann jeder Besucher kostenpflichtige Gemini-Anfragen
auslösen und bei aktivierter Speicherfunktion Objekte im Bucket erzeugen.
Cloud-Run-Instanzlimits setzen keine feste Grenze für diese API- und Speicherkosten.

## Ergebnisse speichern

`BUCKET_NAME` aktiviert die Speicherfunktion. Der Wert ist der Name eines bereits
vorhandenen privaten Buckets, etwa `mein-projekt-gemini-results`, ohne `gs://`.
Im Projekt muss `storage.googleapis.com` aktiviert sein. Der Laufzeit-Service-Account
von Cloud Run braucht `roles/storage.objectCreator` auf diesem Bucket.

Nach einer Antwort erscheint der Button "Ergebnis speichern". Er schreibt eine
neue JSON-Datei mit Prompt, Antwort und UTC-Zeitpunkt nach
`answers/YYYY-MM-DD/<zufällige-ID>.json`. Die Anwendung zeigt danach den
`gs://`-Pfad an. Beim Speichern wird Gemini nicht erneut aufgerufen.

Jeder Speichervorgang erzeugt ein eigenes Objekt. Bestehende Objekte werden durch
eine Erstellungsbedingung vor Überschreiben geschützt. Die App liest oder löscht
keine gespeicherten Objekte; die Dateien bleiben über die Bucket-Berechtigungen
zugänglich. Bei einem Uploadfehler bleibt die Antwort im Formular erhalten.

Das Speicherformular sendet Prompt und Antwort aus dem Browser erneut an den
Server. Der Endpunkt prüft deren Länge und speichert sie als JSON. Die Inhalte
können durch einen geänderten Browserrequest angepasst werden.

## Öffentliches Container-Image

Das Linux-AMD64-Image liegt öffentlich auf GitHub Container Registry:

```text
ghcr.io/it-erben/cloud-run-gemini-demo:latest
```

Der Image-Pfad kann direkt als Container-Image für Cloud Run verwendet werden.
Ein GitHub-Login ist für den Download nicht erforderlich.

```bash
docker pull ghcr.io/it-erben/cloud-run-gemini-demo:latest
```

Jeder erfolgreich geprüfte Push auf `main` veröffentlicht `latest` und
`sha-<vollständige-Commit-SHA>`. Der SHA-Tag oder ein Image-Digest legt den Stand
für ein Deployment fest. Cloud Run übernimmt ein geändertes `latest` erst bei
einem erneuten Deployment.

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

Die Tests prüfen Eingaben, HTML-Ausgabe, Fehlerfälle, den Storage-Upload und die
vom SDK erzeugte Vertex-AI-Anfrage. Cloud-Zugriffe werden simuliert; die Tests
benötigen keine Cloud-Zugangsdaten. GitHub Actions prüft zusätzlich, ob der
Container mit einem abweichenden `PORT` startet.

## Quellen

- [Cloud Run Service Identity](https://docs.cloud.google.com/run/docs/securing/service-identity)
- [Google Gen AI SDK](https://github.com/googleapis/python-genai)
- [Gemini-Modelle und Laufzeiten](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)
- [Container-Images auf Cloud Run deployen](https://docs.cloud.google.com/run/docs/deploying)
- [Cloud-Storage-Objekte aus dem Speicher hochladen](https://docs.cloud.google.com/storage/docs/uploading-objects-from-memory)
- [IAM-Rollen für Cloud Storage](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
