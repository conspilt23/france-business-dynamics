-- 02 — Schémas : `stg` (staging, chargé par Python) et `analytics` (modèle dimensionnel).
-- Idempotent.
USE FranceBusinessDynamics;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'analytics')
    EXEC(N'CREATE SCHEMA analytics');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'stg')
    EXEC(N'CREATE SCHEMA stg');
GO
