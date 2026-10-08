-- 05 — Vues analytiques (`analytics.vw_*`). Idempotent (CREATE OR ALTER, SQL Server 2016 SP1+).
--
-- • vw_EstablishmentSnapshot / vw_LegalUnitSnapshot / vw_AdministrativeEvents : faits + dimensions aplatis.
-- • vw_*By* : agrégations qui répondent aux questions analytiques du README.
-- Pour Power BI, importer plutôt les tables du modèle en étoile (dashboard/power_query.md).
USE FranceBusinessDynamics;
GO

CREATE OR ALTER VIEW analytics.vw_EstablishmentSnapshot AS
SELECT
    d.FullDate              AS SnapshotDate,
    g.CommuneCode, g.CommuneName,
    g.DepartmentCode, g.DepartmentName,
    g.RegionCode, g.RegionName,
    g.GeoStatus,
    a.NafNomenclature, a.NafCode, a.NafSection, a.NafSectionLabel, a.IsNafRev2,
    w.WorkforceCode, w.WorkforceLabel, w.SortOrder AS WorkforceSortOrder,
    f.StatusCode,
    CASE f.StatusCode WHEN 'A' THEN N'Actif' WHEN 'F' THEN N'Fermé' ELSE N'Non renseigné' END AS StatusLabel,
    f.EstablishmentCount
FROM analytics.FactEstablishmentSnapshot AS f
JOIN analytics.DimDate      AS d ON d.DateKey       = f.DateKey
JOIN analytics.DimGeography AS g ON g.CommuneCode   = f.CommuneCode
JOIN analytics.DimActivity  AS a ON a.ActivityKey   = f.ActivityKey
JOIN analytics.DimWorkforce AS w ON w.WorkforceCode = f.WorkforceCode;
GO

CREATE OR ALTER VIEW analytics.vw_LegalUnitSnapshot AS
SELECT
    d.FullDate              AS SnapshotDate,
    a.NafNomenclature, a.NafCode, a.NafSection, a.NafSectionLabel, a.IsNafRev2,
    l.LegalCategoryCode, l.Level1Code, l.Level1Label,
    f.EnterpriseCategory,
    f.StatusCode,
    CASE f.StatusCode WHEN 'A' THEN N'Actif' WHEN 'C' THEN N'Cessé' ELSE N'Non renseigné' END AS StatusLabel,
    f.LegalUnitCount
FROM analytics.FactLegalUnitSnapshot AS f
JOIN analytics.DimDate          AS d ON d.DateKey           = f.DateKey
JOIN analytics.DimActivity      AS a ON a.ActivityKey       = f.ActivityKey
JOIN analytics.DimLegalCategory AS l ON l.LegalCategoryCode = f.LegalCategoryCode;
GO

CREATE OR ALTER VIEW analytics.vw_AdministrativeEvents AS
SELECT
    f.EventYear, f.EntityType, f.EventType,
    a.NafNomenclature, a.NafCode, a.NafSection, a.NafSectionLabel, a.IsNafRev2,
    f.EventCount
FROM analytics.FactAdministrativeEvents AS f
JOIN analytics.DimActivity AS a ON a.ActivityKey = f.ActivityKey;
GO

-- Q : combien d'établissements par région et par état ? (la ligne « Non renseigné » assure la réconciliation)
CREATE OR ALTER VIEW analytics.vw_EstablishmentsByRegion AS
SELECT RegionCode, RegionName, StatusCode, StatusLabel, SUM(EstablishmentCount) AS EstablishmentCount
FROM analytics.vw_EstablishmentSnapshot
GROUP BY RegionCode, RegionName, StatusCode, StatusLabel;
GO

-- Q : quels secteurs (sections NAF Rev.2) ? Les nomenclatures historiques sont isolées, jamais mélangées.
CREATE OR ALTER VIEW analytics.vw_EstablishmentsByNafSection AS
SELECT NafSection, NafSectionLabel, StatusCode, StatusLabel, SUM(EstablishmentCount) AS EstablishmentCount
FROM analytics.vw_EstablishmentSnapshot
GROUP BY NafSection, NafSectionLabel, StatusCode, StatusLabel;
GO

-- Q : quelle taille d'établissement (tranches d'effectifs) ?
CREATE OR ALTER VIEW analytics.vw_EstablishmentsByWorkforce AS
SELECT WorkforceCode, WorkforceLabel, WorkforceSortOrder, StatusCode, StatusLabel,
       SUM(EstablishmentCount) AS EstablishmentCount
FROM analytics.vw_EstablishmentSnapshot
GROUP BY WorkforceCode, WorkforceLabel, WorkforceSortOrder, StatusCode, StatusLabel;
GO

-- Q : quelle évolution annuelle des événements administratifs ? (toujours distinguer EventType)
CREATE OR ALTER VIEW analytics.vw_AdministrativeEventsByYear AS
SELECT EventYear, EntityType, EventType, SUM(EventCount) AS EventCount
FROM analytics.vw_AdministrativeEvents
GROUP BY EventYear, EntityType, EventType;
GO

-- Q : quelle répartition des unités légales par catégorie juridique (niveau I) et catégorie d'entreprise ?
CREATE OR ALTER VIEW analytics.vw_LegalUnitsByLegalCategory AS
SELECT Level1Code, Level1Label, EnterpriseCategory, StatusCode, StatusLabel, SUM(LegalUnitCount) AS LegalUnitCount
FROM analytics.vw_LegalUnitSnapshot
GROUP BY Level1Code, Level1Label, EnterpriseCategory, StatusCode, StatusLabel;
GO
