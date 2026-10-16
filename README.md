# Cloud Run Gemini Demo

Python-Web-App mit Eingabefeld für einen Prompt und Textantwort von Gemini über
Vertex AI. Zwei Tabs speichern Ergebnisse wahlweise in Cloud Storage oder
BigQuery und zeigen die vorhandenen Dateien beziehungsweise Tabellenzeilen an.
Jede Anfrage startet ohne Chatverlauf. Die App nutzt Flask, Gunicorn und die
Google-Clients für Gemini, Cloud Storage und BigQuery.

Google Identity Services meldet Nutzer mit ihrem Google-Konto an. Gemini,
Speichern und Listen sind erst nach der Anmeldung verfügbar.

## Google-Anmeldung

Der OAuth-Client in Google Auth Platform hat den Typ "Webanwendung". Unter
"Autorisierte JavaScript-Quellen" steht die öffentliche HTTPS-Origin der App,
etwa `https://gemini.erben.tech`, ohne Pfad. Für lokale Tests kann
`http://localhost:8080` ergänzt werden. Im Testmodus müssen die Konten als
Testnutzer zugelassen sein.

Der Google-Button öffnet ein Popup. Der Browser sendet das ID-Token anschließend
an `/auth/callback`. Der Server prüft Googles Signatur, Aussteller, Client-ID,
Ablaufzeit und die Nonce der Anmeldesitzung. Die App nutzt weder ein
OAuth-Client-Secret noch eine OAuth-Redirect-URI. Google-Zugriffstokens und
Refresh-Tokens werden nicht angefordert oder gespeichert.

`GOOGLE_OAUTH_CLIENT_ID` enthält die Client-ID mit der Endung
`.apps.googleusercontent.com`. `SESSION_SECRET` signiert die App-Sitzung und
enthält mindestens 32 zufällige Zeichen, zum Beispiel aus `openssl rand -hex 32`.
Der Wert bleibt über Neustarts, Revisionen und Instanzen hinweg gleich.
Eine Änderung meldet bestehende Sitzungen ab. Secrets gehören außerhalb von Git.

Die Sitzung gilt eine Stunde. Ihr Cookie ist `Secure`, `HttpOnly` und
`SameSite=Lax`; die öffentliche App benötigt HTTPS. Schreibende Requests und
die Anmeldung prüfen zusätzlich ein CSRF-Token. "Abmelden" entfernt das lokale
Sitzungscookie. Bereits kopierte Sitzungscookies bleiben bis zum Ablauf gültig.
Fehlt eine der beiden Variablen oder ist das Sitzungsgeheimnis zu kurz, bleibt
die Anwendung gesperrt. `/healthz` bleibt als Startprüfung erreichbar.

Jedes zugelassene Google-Konto kann die App verwenden. Eine eigene Freigabeliste
oder Aufteilung der Daten nach Nutzer besteht nicht. Alle angemeldeten Nutzer
sehen dieselben GCS-Dateien und BigQuery-Zeilen. Der Load Balancer bleibt
öffentlich; IAP wird nicht benötigt. Die App-Anmeldung ersetzt keine IAM-Rollen
des Cloud-Run-Service-Accounts.

## Laufzeit

Der Container lauscht auf `0.0.0.0:$PORT`, standardmäßig Port `8080`.
Der Cloud-Storage-Tab liegt unter `/`, der BigQuery-Tab unter `/bigquery`.
Beide Formulare fordern mit POST an denselben Pfad eine Gemini-Antwort an.
Prompts sind auf 2000 Zeichen begrenzt, Antworten auf 1024 Ausgabetokens.

| Umgebungsvariable        | Bedeutung                                | Standardwert            |
| ------------------------ | ---------------------------------------- | ----------------------- |
| `GOOGLE_CLOUD_PROJECT`   | Projekt-ID für Vertex AI, erforderlich   | keiner                  |
| `GOOGLE_CLOUD_LOCATION`  | Standort des Gemini-Endpunkts            | `global`                |
| `GEMINI_MODEL`           | Gemini-Modell                            | `gemini-3.1-flash-lite` |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth-Client-ID für die Google-Anmeldung | keiner                  |
| `SESSION_SECRET`         | Dauerhaftes Geheimnis für App-Sitzungen  | keiner                  |
| `BUCKET_NAME`            | Name des vorhandenen Ergebnis-Buckets    | keiner                  |
| `BQ_DATASET`             | ID des vorhandenen BigQuery-Datasets     | keiner                  |
| `BQ_TABLE`               | ID der vorhandenen BigQuery-Tabelle      | keiner                  |
| `PORT`                   | HTTP-Port des Containers                 | `8080`                  |

Im Zielprojekt muss `aiplatform.googleapis.com` aktiviert sein. Der an Cloud Run
gebundene Service Account braucht `roles/aiplatform.user` auf diesem Projekt.
Das SDK bezieht seine Zugangsdaten über Application Default Credentials aus der
Cloud-Run-Laufzeit. API-Key und Service-Account-Schlüsseldatei entfallen.

`global` verwendet Googles globalen Endpunkt und garantiert keine Verarbeitung
in einer bestimmten Region. Der Cloud-Run-Standort und der Gemini-Endpunkt werden
getrennt konfiguriert.

Angemeldete Nutzer können kostenpflichtige Gemini-Anfragen auslösen. Konfigurierte
Speicherziele erlauben außerdem das Speichern und Anzeigen von Daten. Die
BigQuery-Liste führt Abfragen aus. Cloud-Run-Instanzlimits begrenzen diese API-, Abfrage- und Speicherkosten
nicht auf einen festen Betrag. Für die Demo eignen sich separate Buckets und
Tabellen mit Übungsdaten.

## Cloud Storage

`BUCKET_NAME` aktiviert die Speicherfunktion. Der Wert ist der Name eines bereits
vorhandenen privaten Buckets, etwa `mein-projekt-gemini-results`, ohne `gs://`.
Im Projekt muss `storage.googleapis.com` aktiviert sein. Der Laufzeit-Service-Account
von Cloud Run braucht `roles/storage.objectCreator` und
`roles/storage.objectViewer` auf diesem Bucket. Die Viewer-Rolle ergänzt das
bisherige Schreibrecht um das Auflisten der Objekte.

Nach einer Antwort erscheint der Button "Ergebnis speichern". Er schreibt eine
neue JSON-Datei mit Prompt, Antwort und UTC-Zeitpunkt nach
`answers/YYYY-MM-DD/<zufällige-ID>.json`. Die Anwendung zeigt danach den
`gs://`-Pfad an. Beim Speichern wird Gemini nicht erneut aufgerufen.

Jeder Speichervorgang erzeugt ein eigenes Objekt. Bestehende Objekte werden durch
eine Erstellungsbedingung vor Überschreiben geschützt. Unter dem Formular
stehen alle aktuellen Objekte des Buckets mit Name, Größe in Bytes und Zeitpunkt
der letzten Änderung. Die Liste umfasst jeweils bis zu 50 Dateien. "Weitere
Einträge" lädt die nächste Seite; "Aktualisieren" beginnt wieder auf der ersten
Seite. Objektinhalte werden weder heruntergeladen noch gelöscht. Bei einem
Uploadfehler bleibt die Antwort im Formular erhalten.

Das Speicherformular sendet Prompt und Antwort aus dem Browser erneut an den
Server. Der Endpunkt prüft deren Länge und speichert sie als JSON. Die Inhalte
können durch einen geänderten Browserrequest angepasst werden.

## BigQuery

`BQ_DATASET` und `BQ_TABLE` aktivieren den BigQuery-Tab. Beide Werte sind einzelne
IDs, zum Beispiel `gemini_results` und `answers`. Zusammen mit
`GOOGLE_CLOUD_PROJECT` ergeben sie `PROJEKT.gemini_results.answers`. Die IDs für
Dataset und Tabelle verwenden Buchstaben, Ziffern und Unterstriche.

Dataset und Tabelle müssen bereits bestehen. Die App legt keine Infrastruktur
an. Die Tabelle braucht dieses Schema, zusätzliche Spalten können nullable sein:

| Spalte       | Typ         | Inhalt                    |
| ------------ | ----------- | ------------------------- |
| `prompt`     | `STRING`    | Eingegebener Prompt       |
| `answer`     | `STRING`    | Gemini-Antwort            |
| `created_at` | `TIMESTAMP` | Zeitpunkt des Speicherns  |

Erforderlich sind `bigquery.googleapis.com` sowie diese Rollen für den
Cloud-Run-Service-Account:

- `roles/bigquery.dataEditor` auf der Zieltabelle oder dem Dataset.
- `roles/bigquery.jobUser` auf dem Projekt für die Abfragen der Liste.

Im BigQuery-Tab erzeugt "Antwort anfordern" zunächst eine Antwort. "Ergebnis
speichern" schreibt Prompt, Antwort und UTC-Zeit als neue Tabellenzeile. Die App
nutzt die Streaming-API und prüft auch zeilenweise zurückgegebene Fehler. Beim
Speichern wird Gemini nicht erneut aufgerufen. Jeder neue Speichervorgang kann
eine weitere Zeile erzeugen; die Streaming-API garantiert keine exakt einmalige
Zustellung.

Die Liste zeigt je Seite bis zu 50 Einträge, neueste zuerst. Antworten lassen
sich über "Antwort anzeigen" aufklappen. "Zurück", "Weitere Einträge" und
"Aktualisieren" navigieren durch die Tabelle. Während andere Nutzer neue Zeilen
speichern, können sich die Seitengrenzen verschieben. Jede Listenabfrage ist auf
100 MiB abrechenbare Daten begrenzt. Größere Abfragen werden abgelehnt und als
Listenfehler angezeigt. BigQuery bestimmt den Standort anhand der Zieltabelle;
`GOOGLE_CLOUD_LOCATION` gilt weiterhin nur für Gemini.

Fehlende Konfiguration wird im jeweiligen Tab angezeigt. Ein Fehler beim Laden
der Liste lässt das Prompt-Formular und die aktuelle Antwort verfügbar. Nach
einem erfolgreichen Upload wird die Liste erneut geladen. Änderungen an
Tabellen oder IAM-Rechten können verzögert sichtbar werden.

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
export GOOGLE_OAUTH_CLIENT_ID="DEINE-CLIENT-ID.apps.googleusercontent.com"
export SESSION_SECRET="$(openssl rand -hex 32)"
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

Die Tests prüfen Anmeldung, Sitzungen, CSRF, Eingaben, HTML-Ausgabe, Fehlerfälle,
beide Speicherziele und das Blättern durch Listen. Signierte Test-Tokens prüfen
die echte Google-Tokenvalidierung einschließlich Signatur, Zielgruppe, Aussteller
und Ablauf. SDK-Tests prüfen die erzeugten Vertex-AI-, Storage-
und BigQuery-Anfragen. Cloud-Zugriffe werden simuliert; die Tests
benötigen keine Cloud-Zugangsdaten. GitHub Actions prüft zusätzlich, ob der
Container mit einem abweichenden `PORT` startet.

## Quellen

- [Google-Anmeldebutton](https://developers.google.com/identity/gsi/web/guides/display-button)
- [Google-ID-Token auf dem Server prüfen](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
- [Cloud Run Service Identity](https://docs.cloud.google.com/run/docs/securing/service-identity)
- [Google Gen AI SDK](https://github.com/googleapis/python-genai)
- [Gemini-Modelle und Laufzeiten](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)
- [Container-Images auf Cloud Run deployen](https://docs.cloud.google.com/run/docs/deploying)
- [Cloud-Storage-Objekte aus dem Speicher hochladen](https://docs.cloud.google.com/storage/docs/uploading-objects-from-memory)
- [IAM-Rollen für Cloud Storage](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
- [Cloud-Storage-Objekte auflisten](https://docs.cloud.google.com/storage/docs/listing-objects)
- [BigQuery Python Client](https://docs.cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.client.Client)
- [Daten in BigQuery streamen](https://docs.cloud.google.com/bigquery/docs/streaming-data-into-bigquery)
- [BigQuery IAM-Rollen](https://docs.cloud.google.com/bigquery/docs/access-control)
