-- 01 — Création de la base (idempotent : sans effet si elle existe déjà).
-- À exécuter connecté à `master`. Voir README, section « SQL Server ».
IF DB_ID(N'FranceBusinessDynamics') IS NULL
BEGIN
    CREATE DATABASE FranceBusinessDynamics;
END;
GO
