"""Vérifie les URL officielles SANS télécharger les fichiers.

Attendu : SIRENE → HTTP 302 (redirection vers le stockage) ; COG → HTTP 200 (fichier direct).
Code de sortie : 0 si tout est conforme, 1 sinon.
"""

from __future__ import annotations

import argparse

from france_business_dynamics.ingestion.sources import REDIRECT_STATUSES, head_stable_url
from france_business_dynamics.utils.config import SOURCE_FILES, SourceFile
from france_business_dynamics.utils.errors import run_cli

# Statut attendu par source (conservé pour compatibilité et tests).
EXPECTED_STATUS: dict[str, int] = {key: src.expected_status for key, src in SOURCE_FILES.items()}


def evaluate_source(source: SourceFile, result: dict) -> tuple[str, str]:
    """Retourne (`OK` | `ATTENTION`, explication). Fonction pure, testable hors ligne."""
    status = result["status_code"]
    location = result.get("location")
    expected = source.expected_status

    if status != expected:
        return "ATTENTION", f"HTTP {status} reçu, HTTP {expected} attendu"
    if expected in REDIRECT_STATUSES:
        if not location:
            return "ATTENTION", "redirection sans en-tête Location"
        if not location.startswith("https://"):
            return "ATTENTION", f"cible de redirection non HTTPS : {location}"
        return "OK", "redirection vers le stockage"
    if location:
        return "ATTENTION", f"redirection inattendue vers {location} (fichier direct attendu)"
    return "OK", "fichier servi directement"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--debug", action="store_true")
    parser.parse_args(argv)

    problems = 0
    for key, source in SOURCE_FILES.items():
        print(f"\n{key}")
        print("-" * len(key))
        print(f"URL stable : {source.stable_url}")
        try:
            result = head_stable_url(source.stable_url)
        except Exception as exc:  # noqa: BLE001 - on veut continuer sur les autres sources
            print(f"ERREUR : {type(exc).__name__}: {exc}")
            problems += 1
            continue

        label, why = evaluate_source(source, result)
        print(f"Statut HTTP : {result['status_code']} (attendu : {source.expected_status}) [{label}] — {why}")
        print(f"Content-Length : {result['content_length']}")
        print(f"Content-Type : {result['content_type']}")
        print(f"Cible de redirection : {result['location'] or 'aucune'}")
        if label != "OK":
            problems += 1

    print()
    if problems:
        print(f"Résultat : {problems} source(s) à examiner.")
        return 1
    print(f"Résultat : {len(SOURCE_FILES)}/{len(SOURCE_FILES)} sources conformes.")
    return 0


if __name__ == "__main__":
    run_cli(main)
