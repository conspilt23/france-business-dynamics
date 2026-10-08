"""Couche B/I — téléchargeur robuste, testé contre un serveur HTTP local (aucun réseau externe)."""

from __future__ import annotations

import hashlib
import os

import pytest

from france_business_dynamics.ingestion.downloader import (
    DownloadFailedError,
    download_file,
    part_paths,
    probe_remote,
)
from france_business_dynamics.utils.errors import PipelineError, PreconditionError
from france_business_dynamics.utils.jsonio import read_json
from tests.support.http_server import FakeSource

pytestmark = [pytest.mark.layer("I-integration"), pytest.mark.integration]

CONTENT = os.urandom(300_000)
SHA = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def source():
    fake = FakeSource(CONTENT).start()
    yield fake
    fake.stop()


def run(source, tmp_path, **kwargs):
    kwargs.setdefault("show_progress", False)
    kwargs.setdefault("chunk_size", 16_384)
    kwargs.setdefault("sleep", lambda _s: None)
    kwargs.setdefault("log", lambda _m: None)
    return download_file(source.stable_url, tmp_path / "data.bin", **kwargs)


def test_probe_resolves_redirect_and_detects_range(source):
    info = probe_remote(source.stable_url)
    assert info.resolved_url.endswith("/files/data.bin")
    assert info.total_bytes == len(CONTENT)
    assert info.accept_ranges is True


def test_probe_detects_server_without_range():
    fake = FakeSource(CONTENT, support_range=False).start()
    try:
        info = probe_remote(fake.stable_url)
        assert info.accept_ranges is False
        assert info.total_bytes == len(CONTENT)
    finally:
        fake.stop()


def test_clean_download_writes_manifest_and_sha256(source, tmp_path):
    result = run(source, tmp_path, key="test_source")
    assert result.status == "downloaded"
    assert result.path.read_bytes() == CONTENT
    manifest = read_json(result.manifest_path)
    assert manifest["sha256"] == SHA
    assert manifest["bytes"] == len(CONTENT)
    assert manifest["stable_url"] == source.stable_url
    assert manifest["resolved_url"].endswith("/files/data.bin")
    assert manifest["key"] == "test_source"
    part, meta, _ = part_paths(result.path)
    assert not part.exists() and not meta.exists()  # aucun résidu


def test_interrupted_stream_is_resumed_with_range(source, tmp_path):
    """Reproduit le cas réel (coupure en cours de flux) : le fichier final doit être identique."""
    source.drop_after.append(120_000)  # 1ère requête corps coupée à 120 000 octets
    result = run(source, tmp_path)
    assert result.path.read_bytes() == CONTENT
    assert result.attempts == 2
    ranges = [r["range"] for r in source.body_requests()]
    assert ranges[0] is None
    # La reprise part de la taille RÉELLE du .part (chunks complets écrits), jamais d'un
    # offset supposé : sinon un trou corromprait le fichier. Le SHA-256 final le prouve.
    resume_offset = int(ranges[1].removeprefix("bytes=").removesuffix("-"))
    assert 0 < resume_offset <= 120_000
    assert read_json(result.manifest_path)["sha256"] == SHA


def test_multiple_interruptions_still_complete(source, tmp_path):
    source.drop_after.extend([50_000, 50_000, 80_000])
    result = run(source, tmp_path, max_retries=5)
    assert result.path.read_bytes() == CONTENT
    assert result.attempts == 4


def test_truncated_file_never_published_after_retries_exhausted(source, tmp_path):
    source.drop_after.extend([10_000] * 10)
    with pytest.raises(DownloadFailedError) as err:
        run(source, tmp_path, max_retries=2)
    destination = tmp_path / "data.bin"
    part, _, manifest = part_paths(destination)
    assert not destination.exists()  # jamais un fichier final incomplet
    assert part.exists()  # le partiel est conservé
    assert not manifest.exists()
    assert "--resume" in (err.value.hint or "")


def test_resume_after_failure_completes_and_is_identical(source, tmp_path):
    source.drop_after.extend([40_000] * 10)
    with pytest.raises(DownloadFailedError):
        run(source, tmp_path, max_retries=1)
    part, _, _ = part_paths(tmp_path / "data.bin")
    kept = part.stat().st_size
    assert 0 < kept < len(CONTENT)

    source.drop_after.clear()
    result = run(source, tmp_path, resume=True)
    assert result.status == "resumed"
    assert result.resumed_from_bytes == kept
    assert result.path.read_bytes() == CONTENT


def test_existing_partial_requires_explicit_choice_and_is_not_deleted(source, tmp_path):
    part, _, _ = part_paths(tmp_path / "data.bin")
    part.write_bytes(CONTENT[:1000])
    with pytest.raises(PreconditionError) as err:
        run(source, tmp_path)
    assert part.exists() and part.stat().st_size == 1000  # rien supprimé
    assert "--resume" in err.value.hint and "--overwrite" in err.value.hint


def test_overwrite_discards_partial_and_redownloads(source, tmp_path):
    part, meta, _ = part_paths(tmp_path / "data.bin")
    part.write_bytes(b"garbage")
    meta.write_text("{}")
    result = run(source, tmp_path, overwrite=True)
    assert result.path.read_bytes() == CONTENT


def test_resume_and_overwrite_are_mutually_exclusive(source, tmp_path):
    with pytest.raises(PreconditionError):
        run(source, tmp_path, resume=True, overwrite=True)


def test_resume_refused_when_server_has_no_range_and_partial_is_kept():
    fake = FakeSource(CONTENT, support_range=False).start()
    try:
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mkdtemp())
        part, _, _ = part_paths(tmp / "data.bin")
        part.write_bytes(CONTENT[:5000])
        with pytest.raises(PipelineError) as err:
            download_file(fake.stable_url, tmp / "data.bin", resume=True, show_progress=False,
                          log=lambda _m: None, sleep=lambda _s: None)
        assert part.exists() and part.stat().st_size == 5000  # NON supprimé
        assert "Range" in err.value.message
        assert "--overwrite" in err.value.hint
    finally:
        fake.stop()


def test_resume_refused_when_remote_version_changed(source, tmp_path):
    part, meta, _ = part_paths(tmp_path / "data.bin")
    part.write_bytes(CONTENT[:5000])
    meta.write_text('{"etag": "\\"OTHER\\"", "total_bytes": %d}' % len(CONTENT))
    with pytest.raises(PipelineError) as err:
        run(source, tmp_path, resume=True)
    assert "a changé" in err.value.message
    assert part.exists()


def test_resume_refused_without_identity_sidecar(source, tmp_path):
    part, _, _ = part_paths(tmp_path / "data.bin")
    part.write_bytes(CONTENT[:5000])
    with pytest.raises(PipelineError) as err:
        run(source, tmp_path, resume=True)
    assert "sidecar" in err.value.message


def test_without_range_support_download_still_works_from_scratch():
    fake = FakeSource(CONTENT, support_range=False).start()
    try:
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mkdtemp())
        fake.drop_after.append(60_000)
        messages: list[str] = []
        result = download_file(fake.stable_url, tmp / "data.bin", show_progress=False,
                               log=messages.append, sleep=lambda _s: None, chunk_size=8192)
        assert result.path.read_bytes() == CONTENT
        assert any("Range" in m for m in messages)  # avertissement + stratégie sûre
    finally:
        fake.stop()


def test_retry_on_503_then_success(source, tmp_path):
    source.forced_status.extend([503, 503])
    result = run(source, tmp_path)
    assert result.path.read_bytes() == CONTENT


def test_404_is_not_retried_and_leaves_no_final_file(source, tmp_path):
    source.forced_status.append(404)
    # La sonde (Range 0-0) consomme le statut forcé : erreur nette, sans nouvelle tentative.
    with pytest.raises(PipelineError) as err:
        run(source, tmp_path)
    assert "404" in err.value.message
    assert not (tmp_path / "data.bin").exists()


def test_existing_complete_file_is_skipped_and_manifest_regenerated(source, tmp_path):
    destination = tmp_path / "data.bin"
    destination.write_bytes(CONTENT)
    result = run(source, tmp_path, key="test_source")
    assert result.status == "skipped_existing"
    assert read_json(result.manifest_path)["sha256"] == SHA
    assert source.body_requests() == []  # aucune requête de corps


def test_verifier_failure_prevents_publication(source, tmp_path):
    def reject(_path):
        raise PipelineError("format invalide")

    with pytest.raises(PipelineError):
        run(source, tmp_path, verify=reject)
    assert not (tmp_path / "data.bin").exists()  # pas de fichier final non vérifié


def test_chunked_response_without_content_length(tmp_path):
    fake = FakeSource(CONTENT, chunked_without_length=True, support_range=False).start()
    try:
        result = download_file(fake.stable_url, tmp_path / "data.bin", show_progress=False,
                               log=lambda _m: None, sleep=lambda _s: None, chunk_size=8192)
        assert result.path.read_bytes() == CONTENT
    finally:
        fake.stop()
