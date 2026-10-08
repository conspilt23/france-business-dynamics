from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from france_business_dynamics.ingestion.check_sources import EXPECTED_STATUS, evaluate_source
from france_business_dynamics.ingestion.sources import diff_snapshots, head_stable_url
from france_business_dynamics.utils.config import SOURCE_FILES

pytestmark = pytest.mark.layer("B-sources")


def test_sirene_sources_expect_redirects_and_cog_is_direct_csv():
    assert EXPECTED_STATUS["stock_unite_legale"] == 302
    assert EXPECTED_STATUS["stock_etablissement"] == 302
    assert EXPECTED_STATUS["stock_unite_legale_historique"] == 302
    assert EXPECTED_STATUS["stock_etablissement_historique"] == 302
    assert EXPECTED_STATUS["cog_communes_2026"] == 200


# --- le checker distingue explicitement 302 (SIRENE) et 200 (COG) -----------------------------
SIRENE = SOURCE_FILES["stock_etablissement"]
COG = SOURCE_FILES["cog_communes_2026"]


def result(status, location=None):
    return {"status_code": status, "location": location, "content_length": None, "content_type": None}


def test_sirene_302_with_https_location_is_ok():
    assert evaluate_source(SIRENE, result(302, "https://object.files.data.gouv.fr/x.parquet"))[0] == "OK"


def test_sirene_200_is_flagged_because_a_redirect_was_expected():
    label, why = evaluate_source(SIRENE, result(200))
    assert label == "ATTENTION" and "302" in why


def test_sirene_302_without_location_or_with_plain_http_is_flagged():
    assert evaluate_source(SIRENE, result(302))[0] == "ATTENTION"
    assert evaluate_source(SIRENE, result(302, "http://insecure/x"))[0] == "ATTENTION"


def test_cog_200_is_ok_and_cog_redirect_is_flagged():
    assert evaluate_source(COG, result(200))[0] == "OK"
    assert evaluate_source(COG, result(302, "https://elsewhere/x.csv"))[0] == "ATTENTION"


def test_unexpected_error_statuses_are_flagged():
    assert evaluate_source(SIRENE, result(404))[0] == "ATTENTION"
    assert evaluate_source(COG, result(503))[0] == "ATTENTION"


# --- head_stable_url ne suit PAS la redirection (serveur local) -------------------------------
@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def do_HEAD(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/file.parquet")
            elif self.path == "/head-not-allowed":
                self.send_response(405)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/fallback")
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_head_does_not_follow_redirect(server):
    info = head_stable_url(f"{server}/redirect")
    assert info["status_code"] == 302
    assert info["location"] == "https://example.invalid/file.parquet"


def test_head_direct_file_returns_200(server):
    assert head_stable_url(f"{server}/file.csv")["status_code"] == 200


def test_head_falls_back_to_get_when_head_is_not_allowed(server):
    info = head_stable_url(f"{server}/head-not-allowed")
    assert info["status_code"] == 302 and info["location"].endswith("/fallback")


# --- dérive de schéma ----------------------------------------------------------------------------
def snapshot(columns, rows=100):
    return {"datasets": {"d": {"rows": rows, "columns": [{"name": n, "type": t} for n, t in columns]}}}


def test_diff_snapshots_detects_added_removed_and_retyped_columns():
    old = snapshot([("a", "string"), ("b", "int64"), ("c", "string")], rows=100)
    new = snapshot([("a", "string"), ("b", "string"), ("d", "bool")], rows=130)
    report = diff_snapshots(old, new)["d"]
    assert report["status"] == "DÉRIVE DE SCHÉMA"
    assert report["added_columns"] == ["d"] and report["removed_columns"] == ["c"]
    assert report["type_changes"] == {"b": ["int64", "string"]}
    assert report["rows_delta"] == 30


def test_diff_snapshots_identical_schema_is_not_drift():
    cols = [("a", "string")]
    assert diff_snapshots(snapshot(cols, 10), snapshot(cols, 15))["d"]["status"] == "IDENTIQUE"


def test_diff_accepts_the_legacy_snapshot_format_names_only_and_count_only():
    legacy = {"datasets": {
        "stock": {"rows": 10, "columns": 2, "column_names": ["a", "b"]},
        "hist": {"rows": 10, "columns": 3},
    }}
    current = {"datasets": {
        "stock": {"rows": 12, "columns": [{"name": "a", "type": "string"}, {"name": "c", "type": "int64"}]},
        "hist": {"rows": 11, "columns": [{"name": n, "type": "string"} for n in "xyz"]},
    }}
    report = diff_snapshots(legacy, current)
    assert report["stock"]["status"] == "DÉRIVE DE SCHÉMA"
    assert report["stock"]["added_columns"] == ["c"] and report["stock"]["removed_columns"] == ["b"]
    assert report["stock"]["type_changes"] == {}  # types inconnus côté référence : pas de faux positif
    assert report["hist"]["status"] == "COLONNES NON COMPARABLES"
    assert report["hist"]["column_count_changed"] is False
