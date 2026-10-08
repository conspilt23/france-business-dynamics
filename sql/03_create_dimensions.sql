-- 03 — Dimensions du modèle analytique (`analytics.Dim*`), créées VIDES : 06_load_mart.sql les remplit.
--
-- ⚠ COMPORTEMENT : script de RECONSTRUCTION. Il supprime puis recrée toutes les tables du schéma
--   `analytics` (faits d'abord, puis dimensions, à cause des clés étrangères). Les données sont
--   régénérables à tout moment depuis `stg.*` (06) ou depuis les Parquet (load_to_db). Rejouer ce
--   script sur une base à ancien schéma (colonnes trop étroites) la remet donc d'équerre.
--   Nécessite SQL Server 2016 ou plus récent (DROP TABLE IF EXISTS).
--
-- Largeurs : codes de commune/département/région varchar(10) — la sentinelle `UNKNOWN` fait 7 caractères.
USE FranceBusinessDynamics;
GO

DROP TABLE IF EXISTS analytics.FactAdministrativeEvents;
DROP TABLE IF EXISTS analytics.FactLegalUnitSnapshot;
DROP TABLE IF EXISTS analytics.FactEstablishmentSnapshot;
DROP TABLE IF EXISTS analytics.DimLegalCategory;
DROP TABLE IF EXISTS analytics.DimWorkforce;
DROP TABLE IF EXISTS analytics.DimActivity;
DROP TABLE IF EXISTS analytics.DimGeography;
DROP TABLE IF EXISTS analytics.DimDate;
GO

CREATE TABLE analytics.DimDate (
    DateKey         int          NOT NULL,   -- AAAAMMJJ (ex. 20261001)
    FullDate        date         NOT NULL,
    CalendarYear    smallint     NOT NULL,
    CalendarQuarter tinyint      NOT NULL,
    CalendarMonth   tinyint      NOT NULL,
    MonthName       nvarchar(20) NOT NULL,
    CONSTRAINT PK_DimDate PRIMARY KEY (DateKey)
);
GO

CREATE TABLE analytics.DimGeography (
    CommuneCode    varchar(10)   NOT NULL,   -- code COG (5 car., ex. 2A001) ou UNKNOWN
    CommuneName    nvarchar(200) NOT NULL,
    DepartmentCode varchar(10)   NOT NULL,   -- 2 à 3 car. ou UNKNOWN
    DepartmentName nvarchar(100) NOT NULL,
    RegionCode     varchar(10)   NOT NULL,
    RegionName     nvarchar(100) NOT NULL,
    GeoStatus      varchar(20)   NOT NULL,   -- MATCHED | UNMATCHED | NO_COMMUNE | NOT_ENRICHED
    CONSTRAINT PK_DimGeography PRIMARY KEY (CommuneCode)
);
GO

-- Une ligne par (nomenclature, code) : un même code peut exister dans deux nomenclatures sans
-- signification commune. NafSection n'est renseignée QUE pour NAFRev2.
CREATE TABLE analytics.DimActivity (
    ActivityKey     int           NOT NULL,
    NafNomenclature varchar(20)   NOT NULL,  -- NAFRev2, NAFRev1, NAF1993, NAP, NAF2025, UNKNOWN
    NafCode         varchar(20)   NOT NULL,
    NafSection      char(1)       NULL,
    NafSectionLabel nvarchar(150) NOT NULL,
    IsNafRev2       bit           NOT NULL,
    CONSTRAINT PK_DimActivity PRIMARY KEY (ActivityKey),
    CONSTRAINT UQ_DimActivity UNIQUE (NafNomenclature, NafCode)
);
GO

CREATE TABLE analytics.DimWorkforce (
    WorkforceCode  varchar(10)   NOT NULL,   -- NN, 00, 01 … 53, UNKNOWN
    WorkforceLabel nvarchar(100) NOT NULL,
    SortOrder      smallint      NOT NULL,   -- ordre croissant de taille (UNKNOWN = 99)
    CONSTRAINT PK_DimWorkforce PRIMARY KEY (WorkforceCode)
);
GO

CREATE TABLE analytics.DimLegalCategory (
    LegalCategoryCode varchar(10)   NOT NULL,  -- code à 4 chiffres ou UNKNOWN
    Level1Code        char(1)       NULL,      -- niveau I : 1er chiffre
    Level1Label       nvarchar(100) NOT NULL,
    CONSTRAINT PK_DimLegalCategory PRIMARY KEY (LegalCategoryCode)
);
GO
