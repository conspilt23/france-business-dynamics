"""Connexion SQL Server (pyodbc) — configuration par variables d'environnement, erreurs lisibles.

`pyodbc` est importé À LA DEMANDE : le reste du paquet (ETL, tests, CI) fonctionne sans pilote ODBC.
Le mot de passe n'est jamais affiché ni journalisé.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from france_business_dynamics.utils.errors import PipelineError, PreconditionError

DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"
TRUE_VALUES = {"1", "true", "yes", "oui", "y"}


@dataclass(frozen=True)
class SqlSettings:
    server: str
    database: str
    driver: str
    trusted: bool
    user: str | None
    password: str | None
    encrypt: bool
    trust_server_certificate: bool

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, load_dotenv_file: bool = True) -> SqlSettings:
        if env is None:
            if load_dotenv_file:
                from dotenv import load_dotenv

                load_dotenv()  # lit .env (jamais committé) si présent
            env = dict(os.environ)

        def flag(name: str, default: str) -> bool:
            return env.get(name, default).strip().lower() in TRUE_VALUES

        settings = cls(
            server=env.get("SQL_SERVER", "localhost").strip(),
            database=env.get("SQL_DATABASE", "FranceBusinessDynamics").strip(),
            driver=env.get("SQL_DRIVER", DEFAULT_DRIVER).strip(),
            trusted=flag("SQL_TRUSTED_CONNECTION", "yes"),
            user=env.get("SQL_USER") or None,
            password=env.get("SQL_PASSWORD") or None,
            encrypt=flag("SQL_ENCRYPT", "no"),
            trust_server_certificate=flag("SQL_TRUST_SERVER_CERTIFICATE", "yes"),
        )
        if not settings.trusted and not (settings.user and settings.password):
            raise PreconditionError(
                "SQL_TRUSTED_CONNECTION=no mais SQL_USER / SQL_PASSWORD sont absents.",
                hint="Renseigner SQL_USER et SQL_PASSWORD dans .env (jamais dans Git), "
                "ou utiliser SQL_TRUSTED_CONNECTION=yes (authentification Windows).",
            )
        return settings

    def connection_string(self, database: str | None = None, *, mask_password: bool = False) -> str:
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={database or self.database}",
            f"Encrypt={'yes' if self.encrypt else 'no'}",
            f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'}",
        ]
        if self.trusted:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.user}")
            parts.append(f"PWD={'***' if mask_password else self.password}")
        return ";".join(parts) + ";"

    def describe(self) -> str:
        auth = "authentification Windows" if self.trusted else f"utilisateur {self.user}"
        return f"{self.server} / {self.database} ({auth}, {self.driver})"


def connect(settings: SqlSettings, database: str | None = None, *, autocommit: bool = False, timeout: int = 15):
    """Ouvre une connexion pyodbc ; toute défaillance devient un message actionnable."""
    try:
        import pyodbc
    except ImportError as exc:
        raise PreconditionError(
            f"Le pilote ODBC système est introuvable pour pyodbc ({exc}).",
            hint="Windows : installer « Microsoft ODBC Driver 18 for SQL Server ». "
            "Linux : installer unixODBC (sudo apt install unixodbc) puis le pilote Microsoft.",
        ) from exc

    drivers = pyodbc.drivers()
    if settings.driver not in drivers:
        raise PreconditionError(
            f"Pilote ODBC « {settings.driver} » non installé. Pilotes détectés : {drivers or 'aucun'}.",
            hint="Installer « Microsoft ODBC Driver 18 for SQL Server » ou renseigner SQL_DRIVER dans .env "
            "avec l'un des pilotes détectés (ex. « ODBC Driver 17 for SQL Server »).",
        )
    try:
        return pyodbc.connect(
            settings.connection_string(database), autocommit=autocommit, timeout=timeout
        )
    except pyodbc.Error as exc:
        raise PipelineError(
            f"Connexion à SQL Server impossible : {settings.describe()}. Détail : {_short(exc)}",
            hint="Vérifier que le service SQL Server est démarré, que SQL_SERVER est correct "
            "(ex. localhost ou localhost\\SQLEXPRESS), puis que la base existe (sql/01_create_database.sql).",
        ) from exc


def _short(exc: BaseException, limit: int = 300) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
