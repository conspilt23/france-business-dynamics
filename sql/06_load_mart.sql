-- 06 — Chargement du modèle analytique depuis le staging.
--
-- PRÉREQUIS (dans l'ordre) : 01 → 05 exécutés, PUIS `python -m france_business_dynamics.load_to_db`
-- (qui remplit stg.*). Ne jamais exécuter ce script avant le chargement : le contrôle ci-dessous
-- l'interrompt avec un message clair si le staging est vide.
--
-- Transaction unique : tout ou rien. Rejouable : vide d'abord `analytics.*` (faits puis dimensions),
-- sans toucher au schéma. Les faits sont agrégés (GROUP BY) : un doublon éventuel du staging est sommé,
-- jamais perdu (la réconciliation de 07_quality_checks.sql le vérifie).
USE FranceBusinessDynamics;
GO

SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM stg.EstablishmentSnapshot)
    AND NOT EXISTS (SELECT 1 FROM stg.LegalUnitSnapshot)
    AND NOT EXISTS (SELECT 1 FROM stg.AdministrativeEvents)
BEGIN
    THROW 50001, N'Staging vide : exécuter `python -m france_business_dynamics.load_to_db` avant 06_load_mart.sql.', 1;
END;
GO

BEGIN TRANSACTION;

-- 0) Vider (ordre des clés étrangères)
DELETE FROM analytics.FactAdministrativeEvents;
DELETE FROM analytics.FactLegalUnitSnapshot;
DELETE FROM analytics.FactEstablishmentSnapshot;
DELETE FROM analytics.DimLegalCategory;
DELETE FROM analytics.DimWorkforce;
DELETE FROM analytics.DimActivity;
DELETE FROM analytics.DimGeography;
DELETE FROM analytics.DimDate;

-- 1) DimDate : une ligne par date de snapshot. Calendrier par arithmétique (indépendant de la langue du serveur).
INSERT INTO analytics.DimDate (DateKey, FullDate, CalendarYear, CalendarQuarter, CalendarMonth, MonthName)
SELECT
    k.date_key,
    DATEFROMPARTS(k.date_key / 10000, (k.date_key / 100) % 100, k.date_key % 100),
    k.date_key / 10000,
    ((k.date_key / 100) % 100 + 2) / 3,
    (k.date_key / 100) % 100,
    CASE (k.date_key / 100) % 100
        WHEN 1 THEN N'janvier'   WHEN 2 THEN N'février'  WHEN 3 THEN N'mars'
        WHEN 4 THEN N'avril'     WHEN 5 THEN N'mai'      WHEN 6 THEN N'juin'
        WHEN 7 THEN N'juillet'   WHEN 8 THEN N'août'     WHEN 9 THEN N'septembre'
        WHEN 10 THEN N'octobre'  WHEN 11 THEN N'novembre' ELSE N'décembre'
    END
FROM (
    SELECT date_key FROM stg.EstablishmentSnapshot
    UNION
    SELECT date_key FROM stg.LegalUnitSnapshot
) AS k;

-- 2) DimGeography : une ligne par code commune du staging ; libellés département/région via les référentiels.
INSERT INTO analytics.DimGeography
    (CommuneCode, CommuneName, DepartmentCode, DepartmentName, RegionCode, RegionName, GeoStatus)
SELECT
    c.commune_code,
    c.commune_name,
    c.department_code,
    COALESCE(rd.department_name, N'Non renseigné'),
    c.region_code,
    COALESCE(rr.region_name, N'Non renseigné'),
    c.geo_status
FROM (
    SELECT commune_code,
           MIN(commune_name)    AS commune_name,
           MIN(department_code) AS department_code,
           MIN(region_code)     AS region_code,
           MIN(geo_status)      AS geo_status
    FROM stg.EstablishmentSnapshot
    GROUP BY commune_code
) AS c
LEFT JOIN stg.RefDepartment AS rd ON rd.department_code = c.department_code
LEFT JOIN stg.RefRegion     AS rr ON rr.region_code     = c.region_code;

-- 3) DimActivity : (nomenclature, code) distincts sur les trois faits ; section NAF Rev.2 uniquement.
INSERT INTO analytics.DimActivity
    (ActivityKey, NafNomenclature, NafCode, NafSection, NafSectionLabel, IsNafRev2)
SELECT
    ROW_NUMBER() OVER (ORDER BY u.naf_nomenclature, u.naf_code),
    u.naf_nomenclature,
    u.naf_code,
    u.naf_section,
    COALESCE(r.naf_section_label, N'Hors NAF Rev.2 ou non classé'),
    CASE WHEN u.naf_nomenclature = 'NAFRev2' AND u.naf_section IS NOT NULL THEN 1 ELSE 0 END
FROM (
    SELECT naf_nomenclature, naf_code, MIN(naf_section) AS naf_section
    FROM (
        SELECT naf_nomenclature, naf_code, naf_section FROM stg.EstablishmentSnapshot
        UNION ALL
        SELECT naf_nomenclature, naf_code, naf_section FROM stg.LegalUnitSnapshot
        UNION ALL
        SELECT naf_nomenclature, naf_code, naf_section FROM stg.AdministrativeEvents
    ) AS x
    GROUP BY naf_nomenclature, naf_code
) AS u
LEFT JOIN stg.RefNafSection AS r ON r.naf_section = u.naf_section;

-- 4) DimWorkforce : référentiel officiel + codes rencontrés mais inconnus du référentiel (ex. UNKNOWN).
INSERT INTO analytics.DimWorkforce (WorkforceCode, WorkforceLabel, SortOrder)
SELECT workforce_code, workforce_label, sort_order
FROM stg.RefWorkforce
UNION ALL
SELECT s.workforce_code, MIN(s.workforce_label), 99
FROM stg.EstablishmentSnapshot AS s
WHERE NOT EXISTS (SELECT 1 FROM stg.RefWorkforce AS r WHERE r.workforce_code = s.workforce_code)
GROUP BY s.workforce_code;

-- 5) DimLegalCategory : codes du staging ; niveau I via le référentiel (1er chiffre).
INSERT INTO analytics.DimLegalCategory (LegalCategoryCode, Level1Code, Level1Label)
SELECT
    c.legal_category_code,
    r.level1_code,
    COALESCE(r.level1_label, N'Non renseigné')
FROM (SELECT DISTINCT legal_category_code FROM stg.LegalUnitSnapshot) AS c
LEFT JOIN stg.RefLegalCategoryLevel1 AS r ON r.level1_code = SUBSTRING(c.legal_category_code, 1, 1);

-- 6) Faits
INSERT INTO analytics.FactEstablishmentSnapshot
    (DateKey, CommuneCode, ActivityKey, WorkforceCode, StatusCode, EstablishmentCount)
SELECT s.date_key, s.commune_code, a.ActivityKey, s.workforce_code, s.status_code, SUM(s.establishment_count)
FROM stg.EstablishmentSnapshot AS s
JOIN analytics.DimActivity AS a ON a.NafNomenclature = s.naf_nomenclature AND a.NafCode = s.naf_code
GROUP BY s.date_key, s.commune_code, a.ActivityKey, s.workforce_code, s.status_code;

INSERT INTO analytics.FactLegalUnitSnapshot
    (DateKey, ActivityKey, LegalCategoryCode, EnterpriseCategory, StatusCode, LegalUnitCount)
SELECT s.date_key, a.ActivityKey, s.legal_category_code, s.enterprise_category, s.status_code, SUM(s.legal_unit_count)
FROM stg.LegalUnitSnapshot AS s
JOIN analytics.DimActivity AS a ON a.NafNomenclature = s.naf_nomenclature AND a.NafCode = s.naf_code
GROUP BY s.date_key, a.ActivityKey, s.legal_category_code, s.enterprise_category, s.status_code;

INSERT INTO analytics.FactAdministrativeEvents (EventYear, EntityType, EventType, ActivityKey, EventCount)
SELECT s.event_year, s.entity_type, s.event_type, a.ActivityKey, SUM(s.event_count)
FROM stg.AdministrativeEvents AS s
JOIN analytics.DimActivity AS a ON a.NafNomenclature = s.naf_nomenclature AND a.NafCode = s.naf_code
GROUP BY s.event_year, s.entity_type, s.event_type, a.ActivityKey;

COMMIT TRANSACTION;
GO
