# Versions logicielles

Versions **exactes** (`==`), fixées dans `pyproject.toml` (source de vérité) et `requirements.txt` — un test vérifie qu'elles
sont identiques. L'arbre complet (transitif, multiplateforme) est dans `requirements-lock.txt`.

| Paquet | Version | Rôle |
|---|---:|---|
| Python | ≥ 3.13, < 3.15 (testé en 3.13 et 3.14) | exécution |
| polars | 1.44.2 | transformations paresseuses |
| pyarrow | 25.0.1 | métadonnées Parquet, lecture par lots |
| requests | 2.34.2 | téléchargement et sondes HTTP |
| tqdm | 4.70.1 | barres de progression |
| pyodbc | 5.3.0 | connexion SQL Server |
| python-dotenv | 1.2.4 | lecture de `.env` |
| fsspec / aiohttp | 2026.9.0 / 3.14.3 | lecture distante du pied de page Parquet |
| jupyter / ipykernel | 1.1.1 / 7.4.0 | notebooks |
| matplotlib | 3.11.2 | graphiques des notebooks |
| nbformat / nbclient | 5.11.1 / 0.11.0 | exécution des notebooks dans les tests |
| pytest | 9.1.1 | tests |
| sqlglot | 30.21.0 | analyse du T-SQL dans les tests |
| setuptools | 84.0.0 | construction du paquet |

Retirés par rapport à la version initiale : `pandas`, `sqlalchemy` (plus utilisés).

L'existence de chaque version a été vérifiée sur PyPI le 2026-10-04, et l'installation testée dans des environnements
propres Python 3.13 et 3.14 (Linux). Python 3.14.6 (poste de l'auteur) n'a pas pu être testé à l'identique : 3.14.4 l'a été.
