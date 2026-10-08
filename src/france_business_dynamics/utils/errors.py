"""Erreurs contrôlées du pipeline : un message clair + une action concrète.

Principe du projet : les échecs doivent être *détectables, explicables et
contrôlés*. Chaque `PipelineError` porte donc ce qui s'est passé (`message`) et
ce qu'il faut faire ensuite (`hint`). Les commandes CLI affichent ces deux
éléments au lieu d'un traceback.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable

EXIT_OK = 0
EXIT_FAILED = 1  # contrôle en échec / erreur d'exécution
EXIT_PRECONDITION = 2  # couche requise absente, option incohérente
EXIT_INTERRUPTED = 130


class PipelineError(Exception):
    """Erreur attendue, expliquable à l'utilisateur."""

    exit_code = EXIT_FAILED

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        lines = [f"ERREUR : {self.message}"]
        if self.hint:
            lines.append(f"  → Action : {self.hint}")
        return "\n".join(lines)


class PreconditionError(PipelineError):
    """Une couche, un fichier ou une configuration requise est absente."""

    exit_code = EXIT_PRECONDITION


class SchemaError(PipelineError):
    """Colonnes ou types inattendus dans une source ou une couche."""


def harden_streams() -> None:
    """Rend stdout/stderr tolérants : un caractère non encodable devient `?` au lieu de faire planter le pipeline.

    Cas réel : sous Windows, une sortie REDIRIGÉE (`commande > log.txt`) utilise la page de codes locale (cp1252),
    qui ne sait pas écrire `→`, `≤`, `≠`… ; sans cela, `UnicodeEncodeError` en plein traitement.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def run_cli(main: Callable[[], int | None], *, debug: bool = False) -> None:
    """Exécute `main()` en convertissant les erreurs en messages lisibles."""
    harden_streams()
    try:
        code = main()
    except PipelineError as exc:
        print(exc.render(), file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        raise SystemExit(EXIT_INTERRUPTED) from None
    except Exception as exc:  # noqa: BLE001 - dernier filet de sécurité
        if debug:
            traceback.print_exc()
        print(
            f"ERREUR INATTENDUE ({type(exc).__name__}) : {exc}\n"
            "  → Action : relancer avec --debug pour obtenir le détail technique, "
            "puis consulter docs/execution_checklist.md.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_FAILED) from None
    raise SystemExit(EXIT_OK if code is None else code)
