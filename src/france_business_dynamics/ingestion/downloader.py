"""Téléchargement robuste et reprenable de gros fichiers (plusieurs Go).

Garanties :
* écriture dans `<fichier>.part` — le fichier final n'existe QUE s'il est complet ;
* nouvelles tentatives avec attente exponentielle (coupures, timeouts, 5xx/429) ;
* reprise via HTTP Range, validée par un sidecar `<fichier>.part.json`
  (URL résolue, taille, ETag, Last-Modified) ;
* contrôle de taille (Content-Length / Content-Range), SHA-256, vérification de format ;
* manifeste JSON atomique à côté du fichier ;
* un fichier partiel n'est JAMAIS supprimé automatiquement.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from tqdm import tqdm

from france_business_dynamics import __version__
from france_business_dynamics.ingestion.verify import sha256_file
from france_business_dynamics.utils.errors import PipelineError, PreconditionError
from france_business_dynamics.utils.jsonio import read_json, write_json_atomic

USER_AGENT = f"france-business-dynamics/{__version__} (projet portfolio; données ouvertes)"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_BACKOFF_SECONDS = 60.0
CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")
CONTENT_RANGE_TOTAL_RE = re.compile(r"^bytes\s+\*/(\d+)$")


class DownloadFailedError(PipelineError):
    """Échec après épuisement des tentatives — le fichier partiel est conservé."""


class _RetryableError(Exception):
    """Interne : erreur transitoire, on peut retenter (avec reprise si possible)."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class _RangeIgnored(Exception):
    """Interne : le serveur a répondu 200 à une requête Range (reprise impossible)."""


@dataclass(frozen=True)
class RemoteInfo:
    stable_url: str
    resolved_url: str
    total_bytes: int | None
    accept_ranges: bool
    etag: str | None
    last_modified: str | None
    content_type: str | None


@dataclass
class DownloadResult:
    status: str  # downloaded | resumed | skipped_existing
    path: Path
    bytes: int
    sha256: str
    manifest_path: Path
    attempts: int = 0
    resumed_from_bytes: int = 0
    manifest: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def part_paths(destination: Path) -> tuple[Path, Path, Path]:
    """(fichier .part, sidecar .part.json, manifeste .json)"""
    return (
        destination.with_name(destination.name + ".part"),
        destination.with_name(destination.name + ".part.json"),
        destination.with_name(destination.name + ".json"),
    )


def human_bytes(value: float | None) -> str:
    if value is None:
        return "taille inconnue"
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if abs(value) < 1024 or unit == "Tio":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} Tio"


def _replace_with_retries(source: Path, target: Path, attempts: int = 5) -> None:
    """`os.replace` avec nouvelles tentatives (antivirus/indexeur sous Windows)."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts:
                raise PipelineError(
                    f"Impossible de renommer {source.name} (fichier verrouillé).",
                    hint="Fermer les programmes qui utilisent le fichier "
                    "(antivirus, explorateur) puis relancer avec --resume.",
                ) from None
            time.sleep(0.5 * attempt)


# --------------------------------------------------------------------------- #
# Sonde distante
# --------------------------------------------------------------------------- #
def probe_remote(
    stable_url: str,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (15.0, 60.0),
    max_retries: int = 3,
    backoff_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RemoteInfo:
    """Résout la redirection et détecte taille + support Range SANS télécharger le corps.

    On envoie `Range: bytes=0-0` : un serveur compatible répond 206 avec
    `Content-Range: bytes 0-0/<total>` ; sinon il répond 200 (Range ignoré).
    Les erreurs transitoires (429/5xx, connexion) sont retentées ; les 4xx définitives non.
    """
    owns_session = session is None
    session = session or build_session()
    try:
        last_reason = "inconnue"
        for attempt in range(1, max_retries + 2):
            try:
                response = session.get(
                    stable_url,
                    headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
                    stream=True,
                    allow_redirects=True,
                    timeout=timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_reason = f"{type(exc).__name__}"
                response = None
            except requests.RequestException as exc:
                raise PipelineError(
                    f"Requête invalide vers {stable_url} ({type(exc).__name__}: {exc}).",
                    hint="Vérifier l'URL puis relancer.",
                ) from exc

            if response is not None:
                try:
                    if response.status_code in RETRYABLE_STATUS:
                        last_reason = f"HTTP {response.status_code}"
                        retry_after = response.headers.get("Retry-After", "")
                        wait = float(retry_after) if retry_after.isdigit() else None
                    else:
                        return _remote_info_from_response(stable_url, response)
                finally:
                    response.close()
            else:
                wait = None

            if attempt > max_retries:
                break
            sleep(wait or min(backoff_seconds * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS))

        raise PipelineError(
            f"Source injoignable après {max_retries + 1} tentatives ({last_reason}) : {stable_url}",
            hint="Vérifier la connexion Internet / proxy, ou réessayer plus tard.",
        )
    finally:
        if owns_session:
            session.close()


def _remote_info_from_response(stable_url: str, response: requests.Response) -> RemoteInfo:
    status = response.status_code
    headers = response.headers
    if status >= 400 and status != 416:
        raise PipelineError(
            f"La source répond HTTP {status} : {stable_url}",
            hint="Lancer `python -m france_business_dynamics.ingestion.check_sources` "
            "pour vérifier les URL officielles.",
        )

    total: int | None = None
    accept_ranges = False
    if status == 206:
        match = CONTENT_RANGE_RE.match(headers.get("Content-Range", "").strip())
        if match and match.group(3) != "*":
            total = int(match.group(3))
            accept_ranges = True
    elif status == 416:
        match = CONTENT_RANGE_TOTAL_RE.match(headers.get("Content-Range", "").strip())
        total = int(match.group(1)) if match else None
        accept_ranges = total is not None
    else:  # 200 : Range ignoré
        length = headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None

    return RemoteInfo(
        stable_url=stable_url,
        resolved_url=response.url,
        total_bytes=total,
        accept_ranges=accept_ranges,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
        content_type=headers.get("Content-Type"),
    )


# --------------------------------------------------------------------------- #
# Téléchargement d'un flux (une tentative)
# --------------------------------------------------------------------------- #
def _stream_into(
    session: requests.Session,
    info: RemoteInfo,
    part: Path,
    offset: int,
    *,
    chunk_size: int,
    timeout: tuple[float, float],
    bar: tqdm,
) -> None:
    headers = {"Accept-Encoding": "identity"}
    if offset > 0:
        headers["Range"] = f"bytes={offset}-"
        if info.etag:
            headers["If-Range"] = info.etag
        elif info.last_modified:
            headers["If-Range"] = info.last_modified

    try:
        response = session.get(info.resolved_url, headers=headers, stream=True, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise _RetryableError(f"connexion impossible ({type(exc).__name__})") from exc

    with response:
        status = response.status_code
        if status in RETRYABLE_STATUS:
            retry_after = response.headers.get("Retry-After", "")
            raise _RetryableError(
                f"HTTP {status}",
                retry_after=float(retry_after) if retry_after.isdigit() else None,
            )
        if status == 416:
            # Plage non satisfaisante : le .part est peut-être déjà complet.
            return
        if status >= 400:
            raise PipelineError(f"La source répond HTTP {status} : {info.resolved_url}")

        if offset > 0:
            if status == 200:
                raise _RangeIgnored()
            match = CONTENT_RANGE_RE.match(response.headers.get("Content-Range", "").strip())
            if status != 206 or not match or int(match.group(1)) != offset:
                raise PipelineError(
                    f"Réponse de reprise incohérente (HTTP {status}, "
                    f"Content-Range={response.headers.get('Content-Range')!r}, offset attendu {offset}).",
                    hint="Relancer avec --overwrite.",
                )

        mode = "ab" if offset > 0 else "wb"
        try:
            with part.open(mode) as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    bar.update(len(chunk))
                handle.flush()
                os.fsync(handle.fileno())
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ContentDecodingError,
        ) as exc:
            # IncompleteRead / connexion coupée en cours de flux (cas observé à ~64 %).
            raise _RetryableError(f"flux interrompu ({type(exc).__name__})") from exc


# --------------------------------------------------------------------------- #
# API principale
# --------------------------------------------------------------------------- #
def download_file(
    stable_url: str,
    destination: Path,
    *,
    key: str | None = None,
    resume: bool = False,
    overwrite: bool = False,
    verify: Callable[[Path], dict] | None = None,
    session: requests.Session | None = None,
    max_retries: int = 5,
    backoff_seconds: float = 2.0,
    timeout: tuple[float, float] = (15.0, 60.0),
    chunk_size: int = 1024 * 1024,
    show_progress: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> DownloadResult:
    """Télécharge `stable_url` vers `destination` de façon sûre. Voir le docstring du module."""
    if resume and overwrite:
        raise PreconditionError("--resume et --overwrite sont incompatibles.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    part, part_meta_path, manifest_path = part_paths(destination)

    # 1) Fichier final déjà présent → on ne retélécharge pas (hors --overwrite).
    if destination.exists() and not overwrite:
        return _accept_existing(destination, manifest_path, key, stable_url, verify, log)

    # 2) Partiel présent sans consigne explicite → on s'arrête SANS rien supprimer.
    if part.exists() and not resume and not overwrite:
        raise PreconditionError(
            f"Un téléchargement partiel existe : {part.name} ({human_bytes(part.stat().st_size)}).",
            hint="Relancer avec --resume pour le reprendre, ou --overwrite pour repartir de zéro. "
            "Rien n'a été supprimé.",
        )

    if overwrite:
        for stale in (part, part_meta_path):
            stale.unlink(missing_ok=True)

    owns_session = session is None
    session = session or build_session()
    started_at = _utc_now()
    started_clock = time.monotonic()
    try:
        info = probe_remote(
            stable_url, session=session, timeout=timeout,
            max_retries=max_retries, backoff_seconds=backoff_seconds, sleep=sleep,
        )
        total = info.total_bytes

        offset = 0
        resumed_from = 0
        part_origin = "new"
        if resume and part.exists():
            offset, part_origin = _validate_resume(part, part_meta_path, info), "resumed"
            resumed_from = offset
            log(f"Reprise de {destination.name} à {human_bytes(offset)} / {human_bytes(total)}.")
        elif resume:
            log("Aucun fichier partiel à reprendre : téléchargement complet.")

        _check_disk_space(destination.parent, total, offset)
        write_json_atomic(
            part_meta_path,
            {
                "stable_url": stable_url,
                "resolved_url": info.resolved_url,
                "total_bytes": total,
                "accept_ranges": info.accept_ranges,
                "etag": info.etag,
                "last_modified": info.last_modified,
                "started_at_utc": started_at,
            },
        )
        if not info.accept_ranges:
            log(
                "Avertissement : le serveur ne supporte pas HTTP Range — en cas de coupure "
                "le téléchargement repartira de zéro (voir README « Téléchargement manuel de secours »)."
            )

        attempts = _download_loop(
            session, info, part, offset, part_origin,
            max_retries=max_retries, backoff_seconds=backoff_seconds, timeout=timeout,
            chunk_size=chunk_size, show_progress=show_progress, sleep=sleep, log=log,
            filename=destination.name,
        )

        # 3) Intégrité : SHA-256 puis vérification de format sur le .part complet.
        size = part.stat().st_size
        with tqdm(total=size, unit="B", unit_scale=True, unit_divisor=1024,
                  desc=f"SHA-256 {destination.name}", disable=not show_progress) as sha_bar:
            digest = sha256_file(part, progress=sha_bar.update)
        verification = verify(part) if verify else {}

        # 4) Publication atomique : le fichier final n'apparaît qu'ici.
        _replace_with_retries(part, destination)
        manifest = {
            "key": key,
            "filename": destination.name,
            "stable_url": stable_url,
            "resolved_url": info.resolved_url,
            "bytes": size,
            "expected_bytes": total,
            "sha256": digest,
            "downloaded_at_utc": _utc_now(),
            "started_at_utc": started_at,
            "duration_seconds": round(time.monotonic() - started_clock, 1),
            "attempts": attempts,
            "resumed": resumed_from > 0 or attempts > 1,
            "resumed_from_bytes": resumed_from,
            "accept_ranges": info.accept_ranges,
            "etag": info.etag,
            "last_modified": info.last_modified,
            "content_type": info.content_type,
            "verification": verification,
            "tool_version": __version__,
        }
        write_json_atomic(manifest_path, manifest)
        part_meta_path.unlink(missing_ok=True)
        return DownloadResult(
            status="resumed" if resumed_from > 0 else "downloaded",
            path=destination, bytes=size, sha256=digest, manifest_path=manifest_path,
            attempts=attempts, resumed_from_bytes=resumed_from, manifest=manifest,
        )
    finally:
        if owns_session:
            session.close()


def _download_loop(
    session, info, part, offset, part_origin, *, max_retries, backoff_seconds, timeout,
    chunk_size, show_progress, sleep, log, filename,
) -> int:
    total = info.total_bytes
    attempts = 0
    with tqdm(total=total, initial=offset, unit="B", unit_scale=True, unit_divisor=1024,
              desc=filename, disable=not show_progress) as bar:
        while True:
            attempts += 1
            try:
                _stream_into(session, info, part, offset, chunk_size=chunk_size,
                             timeout=timeout, bar=bar)
                size = part.stat().st_size if part.exists() else 0
                if total is None:
                    return attempts  # taille inconnue : on se fie à la fin de flux + vérification de format
                if size == total:
                    return attempts
                if size > total:
                    raise PipelineError(
                        f"{part.name} dépasse la taille annoncée ({size} > {total} octets).",
                        hint="Le fichier partiel est conservé ; relancer avec --overwrite.",
                    )
                raise _RetryableError(f"fichier incomplet ({size}/{total} octets)")
            except _RangeIgnored:
                if part_origin == "resumed":
                    raise PipelineError(
                        "Le serveur a ignoré la requête Range : reprise impossible "
                        "(la source a peut-être changé).",
                        hint="Le fichier partiel est conservé. Relancer avec --overwrite pour repartir "
                        "de zéro, ou télécharger le fichier manuellement dans data/raw/.",
                    ) from None
                log("Le serveur n'a pas honoré Range : reprise depuis le début.")
                bar.reset(total=total)
                offset = 0
                part.unlink(missing_ok=True)  # partiel créé par CE lancement, non reprenable
                part_origin = "new"
            except _RetryableError as exc:
                if attempts > max_retries:
                    got = part.stat().st_size if part.exists() else 0
                    raise DownloadFailedError(
                        f"Échec après {attempts} tentatives ({exc}). "
                        f"Fichier partiel conservé : {part.name} ({human_bytes(got)} / {human_bytes(total)}).",
                        hint="Relancer la même commande avec --resume.",
                    ) from None
                offset = part.stat().st_size if part.exists() else 0
                if not info.accept_ranges and offset > 0:
                    log("Reprise impossible (pas de Range) : retour au début.")
                    offset = 0
                    part.unlink(missing_ok=True)
                    bar.reset(total=total)
                delay = exc.retry_after or min(backoff_seconds * 2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
                log(f"Tentative {attempts}/{max_retries + 1} interrompue ({exc}). "
                    f"Reprise à {human_bytes(offset)} dans {delay:.0f}s…")
                sleep(delay)


def _validate_resume(part: Path, part_meta_path: Path, info: RemoteInfo) -> int:
    """Contrôle qu'un .part peut être repris sans corrompre le fichier. Retourne l'offset."""
    size = part.stat().st_size
    if not info.accept_ranges:
        raise PipelineError(
            "Le serveur ne supporte pas HTTP Range : impossible de reprendre ce fichier partiel.",
            hint="Stratégie sûre : conserver le partiel, puis (a) relancer avec --overwrite pour "
            "repartir de zéro, ou (b) télécharger le fichier manuellement dans data/raw/ "
            "(il sera vérifié et accepté au prochain lancement).",
        )
    if not part_meta_path.exists():
        raise PipelineError(
            f"{part.name} n'a pas de sidecar d'identité ({part_meta_path.name}) : "
            "impossible de garantir qu'il provient de la même version de la source.",
            hint="Relancer avec --overwrite.",
        )
    meta = read_json(part_meta_path)
    changed = []
    if meta.get("etag") and info.etag and meta["etag"] != info.etag:
        changed.append("ETag")
    if meta.get("last_modified") and info.last_modified and meta["last_modified"] != info.last_modified:
        changed.append("Last-Modified")
    if meta.get("total_bytes") and info.total_bytes and meta["total_bytes"] != info.total_bytes:
        changed.append("taille")
    if changed:
        raise PipelineError(
            f"La source a changé depuis le début du téléchargement ({', '.join(changed)}).",
            hint="Le partiel est conservé mais inutilisable : relancer avec --overwrite.",
        )
    if info.total_bytes is not None and size > info.total_bytes:
        raise PipelineError(
            f"{part.name} est plus grand que la source ({size} > {info.total_bytes}).",
            hint="Relancer avec --overwrite.",
        )
    return size


def _check_disk_space(directory: Path, total: int | None, offset: int) -> None:
    if total is None:
        return
    needed = int((total - offset) * 1.02)
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise PreconditionError(
            f"Espace disque insuffisant : {human_bytes(needed)} requis, {human_bytes(free)} disponibles "
            f"dans {directory}.",
            hint="Libérer de l'espace ou définir FBD_DATA_DIR vers un autre disque.",
        )


def _accept_existing(destination, manifest_path, key, stable_url, verify, log) -> DownloadResult:
    """Fichier final déjà présent : on le vérifie (format) et on garantit un manifeste."""
    size = destination.stat().st_size
    verification = verify(destination) if verify else {}
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get("bytes") == size:
            return DownloadResult("skipped_existing", destination, size,
                                  manifest.get("sha256", ""), manifest_path, manifest=manifest)
        log(f"Manifeste incohérent avec {destination.name} (taille différente) : régénéré.")
    log(f"{destination.name} déjà présent : vérification locale et génération du manifeste.")
    digest = sha256_file(destination)
    manifest = {
        "key": key, "filename": destination.name, "stable_url": stable_url,
        "resolved_url": None, "bytes": size, "expected_bytes": None, "sha256": digest,
        "downloaded_at_utc": None, "note": "fichier présent localement ; manifeste régénéré",
        "verification": verification, "tool_version": __version__,
    }
    write_json_atomic(manifest_path, manifest)
    return DownloadResult("skipped_existing", destination, size, digest, manifest_path, manifest=manifest)
