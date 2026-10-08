from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.transformation.marts import (
    current_establishment_snapshot,
    current_establishment_snapshot_from_curated,
    legal_unit_current_snapshot,
)
from france_business_dynamics.utils.config import UNKNOWN_CODE, UNKNOWN_LABEL

pytestmark = pytest.mark.layer("E-transformation")


def test_current_establishment_snapshot_builds_without_alias_error(tmp_path):
    source = tmp_path / "stock_etablissement.parquet"
    cog = tmp_path / "v_commune_2026.csv"

    pl.DataFrame(
        {
            "siren": ["123456789"],
            "nic": ["00001"],
            "siret": ["12345678900001"],
            "dateCreationEtablissement": ["2022-01-15"],
            "trancheEffectifsEtablissement": ["11"],
            "anneeEffectifsEtablissement": [2024],
            "etablissementSiege": [True],
            "codePostalEtablissement": ["01000"],
            "libelleCommuneEtablissement": ["Bourg-en-Bresse"],
            "codeCommuneEtablissement": ["01004"],
            "dateDebut": ["2022-01-15"],
            "etatAdministratifEtablissement": ["A"],
            "activitePrincipaleEtablissement": ["01.11Z"],
            "nomenclatureActivitePrincipaleEtablissement": ["NAFRev2"],
            "caractereEmployeurEtablissement": ["O"],
            "activitePrincipaleNAF25Etablissement": ["01.11Y"],
        }
    ).write_parquet(source)

    cog.write_text(
        "TYPECOM,COM,REG,DEP,CTCD,ARR,TNCC,NCC,NCCENR,LIBELLE,CAN,COMPARENT\n"
        "COM,01004,84,01,01,01,0,AMBERIEU EN BUGEY,Ambérieu-en-Bugey,Ambérieu-en-Bugey,01,\n",
        encoding="utf-8",
    )

    result = current_establishment_snapshot(source, cog).collect()

    assert result.height == 1
    assert result["naf_nomenclature"][0] == "NAFRev2"
    assert result["department_code"][0] == "01"
    assert result["region_code"][0] == "84"


def test_mart_from_raw_equals_mart_from_curated_layer(fixture_string):
    """Un seul chemin de transformation : le mart ne duplique pas la logique de nettoyage."""
    from france_business_dynamics.utils.config import SOURCE_FILES

    raw = SOURCE_FILES["stock_etablissement"].local_path(fixture_string)
    cog = SOURCE_FILES["cog_communes_2026"].local_path(fixture_string)
    direct = current_establishment_snapshot(raw, cog).collect()
    layered = current_establishment_snapshot_from_curated(fixture_string.processed / "etablissement_current.parquet").collect()
    assert direct.sort(direct.columns).equals(layered.sort(layered.columns))


def test_limit_restricts_the_rows_read(fixture_string):
    curated = fixture_string.processed / "etablissement_current.parquet"
    assert current_establishment_snapshot_from_curated(curated, limit=3).collect()["establishment_count"].sum() == 3
    assert legal_unit_current_snapshot(fixture_string.processed / "unite_legale_current.parquet", limit=5).collect()[
        "legal_unit_count"].sum() == 5


def test_missing_values_use_documented_sentinels(fixture_string):
    mart = current_establishment_snapshot_from_curated(fixture_string.processed / "etablissement_current.parquet").collect()
    row = mart.filter(pl.col("commune_code") == UNKNOWN_CODE).to_dicts()[0]
    assert row["commune_name"] == UNKNOWN_LABEL and row["department_code"] == UNKNOWN_CODE
    assert mart.filter(pl.col("naf_code") == UNKNOWN_CODE).height >= 1
    assert mart.filter(pl.col("workforce_code") == UNKNOWN_CODE)["workforce_label"].unique().to_list() == [UNKNOWN_LABEL]
    assert mart.null_count().drop("naf_section").to_numpy().sum() == 0  # seule naf_section peut être nulle


def test_unknown_sentinels_fit_the_sql_widths():
    """Chaque colonne pouvant recevoir une sentinelle doit être assez large (cause du bug SQL historique)."""
    from france_business_dynamics.sql_contract import BY_NAME
    from france_business_dynamics.utils.config import UNKNOWN_LABEL, UNKNOWN_STATUS

    code_columns = {
        "EstablishmentSnapshot": ["commune_code", "department_code", "region_code", "naf_nomenclature",
                                  "naf_code", "workforce_code"],
        "LegalUnitSnapshot": ["naf_nomenclature", "naf_code", "legal_category_code", "enterprise_category"],
        "AdministrativeEvents": ["naf_nomenclature", "naf_code"],
    }
    for table, columns in code_columns.items():
        for name in columns:
            assert BY_NAME[table].column(name).length >= len(UNKNOWN_CODE), (table, name)
    assert BY_NAME["EstablishmentSnapshot"].column("status_code").length >= len(UNKNOWN_STATUS)
    assert BY_NAME["EstablishmentSnapshot"].column("commune_name").length >= len(UNKNOWN_LABEL)
    assert BY_NAME["EstablishmentSnapshot"].column("workforce_label").length >= len(UNKNOWN_LABEL)
