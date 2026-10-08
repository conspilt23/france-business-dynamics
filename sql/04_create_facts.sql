-- 04 — Staging (`stg.*`, chargé par `python -m france_business_dynamics.load_to_db`) et faits (`analytics.Fact*`).
--
-- ⚠ COMPORTEMENT : script de RECONSTRUCTION (DROP puis CREATE) — voir 03. Il remplace aussi les
--   anciennes tables `stg.*` trop étroites (cause de l'erreur « String data, right truncation »).
--
-- STAGING : volontairement LARGE et permissif (aucune clé, aucun CHECK). Le contrôle de qualité vit
-- dans le quality gate Python et dans les contraintes de `analytics.*`.
-- Ces définitions sont la copie exacte de `src/france_business_dynamics/sql_contract.py` : un test
-- compare ce fichier au contrat, et `load_to_db` revérifie le schéma VIVANT avant d'insérer.
USE FranceBusinessDynamics;
GO

-- ---------------------------------------------------------------- STAGING : faits
-- Établissements par commune × activité × tranche d'effectifs × état.
DROP TABLE IF EXISTS stg.EstablishmentSnapshot;
GO
CREATE TABLE stg.EstablishmentSnapshot (
    date_key int NOT NULL,
    commune_code varchar(10) NOT NULL,
    commune_name nvarchar(200) NOT NULL,
    department_code varchar(10) NOT NULL,
    region_code varchar(10) NOT NULL,
    geo_status varchar(20) NOT NULL,
    naf_nomenclature varchar(20) NOT NULL,
    naf_code varchar(20) NOT NULL,
    naf_section char(1) NULL,
    workforce_code varchar(10) NOT NULL,
    workforce_label nvarchar(100) NOT NULL,
    status_code varchar(5) NOT NULL,
    establishment_count bigint NOT NULL
);
GO

-- Unités légales par activité × catégorie juridique × catégorie d'entreprise × état.
DROP TABLE IF EXISTS stg.LegalUnitSnapshot;
GO
CREATE TABLE stg.LegalUnitSnapshot (
    date_key int NOT NULL,
    naf_nomenclature varchar(20) NOT NULL,
    naf_code varchar(20) NOT NULL,
    naf_section char(1) NULL,
    legal_category_code varchar(10) NOT NULL,
    enterprise_category varchar(10) NOT NULL,
    status_code varchar(5) NOT NULL,
    legal_unit_count bigint NOT NULL
);
GO

-- Changements d'état administratif et dates de création enregistrées, par année.
DROP TABLE IF EXISTS stg.AdministrativeEvents;
GO
CREATE TABLE stg.AdministrativeEvents (
    event_year smallint NOT NULL,
    entity_type nvarchar(40) NOT NULL,
    event_type nvarchar(60) NOT NULL,
    naf_nomenclature varchar(20) NOT NULL,
    naf_code varchar(20) NOT NULL,
    naf_section char(1) NULL,
    event_count bigint NOT NULL
);
GO

-- ---------------------------------------------------------------- STAGING : référentiels (libellés)
-- Libellés des régions (référentiel statique du dépôt).
DROP TABLE IF EXISTS stg.RefRegion;
GO
CREATE TABLE stg.RefRegion (
    region_code varchar(10) NOT NULL,
    region_name nvarchar(100) NOT NULL
);
GO

-- Libellés des départements.
DROP TABLE IF EXISTS stg.RefDepartment;
GO
CREATE TABLE stg.RefDepartment (
    department_code varchar(10) NOT NULL,
    department_name nvarchar(100) NOT NULL
);
GO

-- Libellés des 21 sections NAF Rev.2.
DROP TABLE IF EXISTS stg.RefNafSection;
GO
CREATE TABLE stg.RefNafSection (
    naf_section char(1) NOT NULL,
    naf_section_label nvarchar(150) NOT NULL
);
GO

-- Catégories juridiques — niveau I.
DROP TABLE IF EXISTS stg.RefLegalCategoryLevel1;
GO
CREATE TABLE stg.RefLegalCategoryLevel1 (
    level1_code char(1) NOT NULL,
    level1_label nvarchar(100) NOT NULL
);
GO

-- Tranches d'effectifs et ordre de tri.
DROP TABLE IF EXISTS stg.RefWorkforce;
GO
CREATE TABLE stg.RefWorkforce (
    workforce_code varchar(10) NOT NULL,
    workforce_label nvarchar(100) NOT NULL,
    sort_order smallint NOT NULL
);
GO

-- ---------------------------------------------------------------- ANALYTICS : faits
-- Clés étrangères vers les dimensions ; contraintes CHECK = validation métier stricte.
CREATE TABLE analytics.FactEstablishmentSnapshot (
    DateKey            int         NOT NULL,
    CommuneCode        varchar(10) NOT NULL,
    ActivityKey        int         NOT NULL,
    WorkforceCode      varchar(10) NOT NULL,
    StatusCode         char(1)     NOT NULL,   -- A actif · F fermé · U non renseigné
    EstablishmentCount bigint      NOT NULL,
    CONSTRAINT PK_FactEstablishmentSnapshot
        PRIMARY KEY (DateKey, CommuneCode, ActivityKey, WorkforceCode, StatusCode),
    CONSTRAINT FK_FES_Date      FOREIGN KEY (DateKey)       REFERENCES analytics.DimDate (DateKey),
    CONSTRAINT FK_FES_Geography FOREIGN KEY (CommuneCode)   REFERENCES analytics.DimGeography (CommuneCode),
    CONSTRAINT FK_FES_Activity  FOREIGN KEY (ActivityKey)   REFERENCES analytics.DimActivity (ActivityKey),
    CONSTRAINT FK_FES_Workforce FOREIGN KEY (WorkforceCode) REFERENCES analytics.DimWorkforce (WorkforceCode),
    CONSTRAINT CK_FES_Status    CHECK (StatusCode IN ('A', 'F', 'U')),
    CONSTRAINT CK_FES_Count     CHECK (EstablishmentCount > 0)
);
GO

CREATE TABLE analytics.FactLegalUnitSnapshot (
    DateKey            int         NOT NULL,
    ActivityKey        int         NOT NULL,
    LegalCategoryCode  varchar(10) NOT NULL,
    EnterpriseCategory varchar(10) NOT NULL,   -- PME · ETI · GE · UNKNOWN
    StatusCode         char(1)     NOT NULL,   -- A actif · C cessé · U non renseigné
    LegalUnitCount     bigint      NOT NULL,
    CONSTRAINT PK_FactLegalUnitSnapshot
        PRIMARY KEY (DateKey, ActivityKey, LegalCategoryCode, EnterpriseCategory, StatusCode),
    CONSTRAINT FK_FLU_Date      FOREIGN KEY (DateKey)           REFERENCES analytics.DimDate (DateKey),
    CONSTRAINT FK_FLU_Activity  FOREIGN KEY (ActivityKey)       REFERENCES analytics.DimActivity (ActivityKey),
    CONSTRAINT FK_FLU_Legal     FOREIGN KEY (LegalCategoryCode) REFERENCES analytics.DimLegalCategory (LegalCategoryCode),
    CONSTRAINT CK_FLU_Status    CHECK (StatusCode IN ('A', 'C', 'U')),
    CONSTRAINT CK_FLU_Count     CHECK (LegalUnitCount > 0)
);
GO

-- ⚠ Les trois EventType ne s'additionnent pas (voir docs/methodology.md) : toujours filtrer sur EventType.
CREATE TABLE analytics.FactAdministrativeEvents (
    EventYear   smallint     NOT NULL,
    EntityType  nvarchar(40) NOT NULL,
    EventType   nvarchar(60) NOT NULL,
    ActivityKey int          NOT NULL,
    EventCount  bigint       NOT NULL,
    CONSTRAINT PK_FactAdministrativeEvents PRIMARY KEY (EventYear, EntityType, EventType, ActivityKey),
    CONSTRAINT FK_FAE_Activity  FOREIGN KEY (ActivityKey) REFERENCES analytics.DimActivity (ActivityKey),
    CONSTRAINT CK_FAE_Count     CHECK (EventCount > 0)
);
GO
