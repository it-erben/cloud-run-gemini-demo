import os
import re
from uuid import uuid4

from google.cloud import bigquery, storage

PAGE_SIZE = 50


def bigquery_table():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    dataset = os.environ.get("BQ_DATASET", "").strip()
    table = os.environ.get("BQ_TABLE", "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", project) or not all(
        re.fullmatch(r"[a-zA-Z0-9_]+", name) for name in (dataset, table)
    ):
        raise ValueError("Ungültige BigQuery-Konfiguration")
    return f"{project}.{dataset}.{table}"


def list_objects(bucket_name, page_token=None):
    with storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")) as client:
        iterator = client.list_blobs(
            bucket_name,
            page_size=PAGE_SIZE,
            max_results=PAGE_SIZE,
            page_token=page_token,
            timeout=10,
            retry=None,
        )
        objects = list(next(iterator.pages, []))
        return objects, iterator.next_page_token


def list_bigquery_rows(page):
    table = bigquery_table()
    with bigquery.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]) as client:
        rows = list(
            client.query_and_wait(
                f"SELECT prompt, answer, created_at FROM `{table}` "
                "ORDER BY created_at DESC, prompt, answer LIMIT 51 OFFSET @offset",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "offset", "INT64", page * PAGE_SIZE
                        )
                    ],
                    maximum_bytes_billed=100 * 1024 * 1024,
                ),
                page_size=PAGE_SIZE + 1,
                max_results=PAGE_SIZE + 1,
                api_timeout=10,
                wait_timeout=15,
                retry=None,
                job_retry=None,
            )
        )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def save_bigquery_row(document):
    table = bigquery_table()
    with bigquery.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]) as client:
        errors = client.insert_rows_json(
            table, [document], row_ids=[uuid4().hex], timeout=15, retry=None
        )
        if errors:
            raise ValueError(f"BigQuery hat die Zeile abgelehnt: {errors}")
    return table
