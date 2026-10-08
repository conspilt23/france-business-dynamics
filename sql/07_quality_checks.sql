-- 07 — Contrôles de qualité SQL (à exécuter APRÈS le chargement Python et 06_load_mart.sql).
--
-- Retourne UN jeu de résultats : CheckName | Status (PASS · WARN · FAIL) | Observed | Expected.
-- Aucune erreur levée : lire la colonne Status. `python -m france_business_dynamics.sql_runner`
-- interprète ce jeu de résultats (code de sortie 1 s'il contient un FAIL).
USE FranceBusinessDynamics;
GO

SELECT CheckName, Status, Observed, Expected
FROM (
    -- 1. Les faits ne sont pas vides (établissements : obligatoire ; autres : avertissement si étape non exécutée)
    SELECT 10 AS Ord, 'fact_establishments_not_empty' AS CheckName,
           CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'FAIL' END AS Status,
           CAST(COUNT(*) AS varchar(40)) AS Observed, '> 0' AS Expected
    FROM analytics.FactEstablishmentSnapshot
    UNION ALL
    SELECT 11, 'fact_legal_units_not_empty',
           CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'WARN' END, CAST(COUNT(*) AS varchar(40)), '> 0'
    FROM analytics.FactLegalUnitSnapshot
    UNION ALL
    SELECT 12, 'fact_events_not_empty',
           CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'WARN' END, CAST(COUNT(*) AS varchar(40)), '> 0'
    FROM analytics.FactAdministrativeEvents

    -- 2. Réconciliation staging → faits : aucune perte, aucun doublon fantôme
    UNION ALL
    SELECT 20, 'reconciliation_establishments',
           CASE WHEN COALESCE(f.total, 0) = COALESCE(s.total, 0) THEN 'PASS' ELSE 'FAIL' END,
           CAST(COALESCE(f.total, 0) AS varchar(40)), CAST(COALESCE(s.total, 0) AS varchar(40))
    FROM (SELECT SUM(establishment_count) AS total FROM stg.EstablishmentSnapshot) AS s
    CROSS JOIN (SELECT SUM(EstablishmentCount) AS total FROM analytics.FactEstablishmentSnapshot) AS f
    UNION ALL
    SELECT 21, 'reconciliation_legal_units',
           CASE WHEN COALESCE(f.total, 0) = COALESCE(s.total, 0) THEN 'PASS' ELSE 'FAIL' END,
           CAST(COALESCE(f.total, 0) AS varchar(40)), CAST(COALESCE(s.total, 0) AS varchar(40))
    FROM (SELECT SUM(legal_unit_count) AS total FROM stg.LegalUnitSnapshot) AS s
    CROSS JOIN (SELECT SUM(LegalUnitCount) AS total FROM analytics.FactLegalUnitSnapshot) AS f
    UNION ALL
    SELECT 22, 'reconciliation_events',
           CASE WHEN COALESCE(f.total, 0) = COALESCE(s.total, 0) THEN 'PASS' ELSE 'FAIL' END,
           CAST(COALESCE(f.total, 0) AS varchar(40)), CAST(COALESCE(s.total, 0) AS varchar(40))
    FROM (SELECT SUM(event_count) AS total FROM stg.AdministrativeEvents) AS s
    CROSS JOIN (SELECT SUM(EventCount) AS total FROM analytics.FactAdministrativeEvents) AS f

    -- 3. Intégrité référentielle (doublon des clés étrangères, vérifié explicitement)
    UNION ALL
    SELECT 30, 'no_orphan_dimension_keys',
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END, CAST(COUNT(*) AS varchar(40)), '0'
    FROM analytics.FactEstablishmentSnapshot AS f
    WHERE NOT EXISTS (SELECT 1 FROM analytics.DimGeography g WHERE g.CommuneCode = f.CommuneCode)
       OR NOT EXISTS (SELECT 1 FROM analytics.DimActivity a WHERE a.ActivityKey = f.ActivityKey)
       OR NOT EXISTS (SELECT 1 FROM analytics.DimWorkforce w WHERE w.WorkforceCode = f.WorkforceCode)
       OR NOT EXISTS (SELECT 1 FROM analytics.DimDate d WHERE d.DateKey = f.DateKey)

    -- 4. Méthodologie : aucune section NAF Rev.2 hors NAFRev2
    UNION ALL
    SELECT 40, 'no_rev2_section_outside_nafrev2',
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END, CAST(COUNT(*) AS varchar(40)), '0'
    FROM analytics.DimActivity
    WHERE NafNomenclature <> 'NAFRev2' AND NafSection IS NOT NULL

    -- 5. Les attributs géographiques dépendent bien du code commune (sinon MIN() masquerait une incohérence)
    UNION ALL
    SELECT 50, 'geography_attributes_functionally_dependent',
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END, CAST(COUNT(*) AS varchar(40)), '0'
    FROM (
        SELECT commune_code
        FROM stg.EstablishmentSnapshot
        GROUP BY commune_code
        HAVING COUNT(DISTINCT commune_name) > 1
            OR COUNT(DISTINCT department_code) > 1
            OR COUNT(DISTINCT region_code) > 1
            OR COUNT(DISTINCT geo_status) > 1
    ) AS bad

    -- 6. Qualité de la géographie (avertissements)
    UNION ALL
    SELECT 60, 'unknown_commune_share_le_1pct',
           CASE WHEN COALESCE(SUM(CASE WHEN CommuneCode = 'UNKNOWN' THEN EstablishmentCount END), 0) * 100
                     <= COALESCE(SUM(EstablishmentCount), 0) THEN 'PASS' ELSE 'WARN' END,
           CAST(COALESCE(SUM(CASE WHEN CommuneCode = 'UNKNOWN' THEN EstablishmentCount END), 0) AS varchar(40)),
           '<= 1% de ' + CAST(COALESCE(SUM(EstablishmentCount), 0) AS varchar(40))
    FROM analytics.FactEstablishmentSnapshot
    UNION ALL
    SELECT 61, 'region_label_missing_for_known_code',
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'WARN' END, CAST(COUNT(*) AS varchar(40)), '0'
    FROM analytics.DimGeography
    WHERE RegionCode <> 'UNKNOWN' AND RegionName = N'Non renseigné'

    -- 7. Calendrier et fenêtre d'analyse
    UNION ALL
    SELECT 70, 'dim_date_populated',
           CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'FAIL' END, CAST(COUNT(*) AS varchar(40)), '> 0'
    FROM analytics.DimDate
    UNION ALL
    SELECT 71, 'event_years_within_analysis_window',
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END, CAST(COUNT(*) AS varchar(40)), '0'
    FROM analytics.FactAdministrativeEvents
    WHERE EventYear < 2021 OR EventYear > 2025
) AS checks
ORDER BY Ord;
GO
