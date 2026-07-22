-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_year_of_registration.sql
-- Run against the LIVE westlake_insurance database.
-- Safe to run and re-run: checks information_schema before adding
-- the column, same pattern as migrations_add_indexes.sql, since
-- this MySQL version doesn't support ADD COLUMN IF NOT EXISTS.
-- No DROP statements anywhere in this file.
--
-- Adds year_of_registration, distinct from the existing
-- year_of_manufacture column. DMVIC's Type A/B/C certificate
-- issuance endpoints all require Yearofregistration as a
-- mandatory field, separate from Yearofmanufacture — a vehicle
-- can be manufactured in one year and registered/imported into
-- Kenya in a later one.
--
-- Run: mysql -u root -p westlake_insurance < migrations_add_year_of_registration.sql
-- Recommended first: mysqldump -u root -p westlake_insurance > backup.sql
-- ============================================================

USE westlake_insurance;

SET @db := DATABASE();

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='quotations' AND COLUMN_NAME='year_of_registration') = 0,
    'ALTER TABLE quotations ADD COLUMN year_of_registration SMALLINT UNSIGNED NULL DEFAULT NULL
        COMMENT ''Year vehicle was registered, may differ from year_of_manufacture; required by DMVIC''
        AFTER year_of_manufacture',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;