-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_quotation_id_postal.sql
-- Run against the LIVE westlake_insurance database.
--
-- Adds id_number and postal_address to the quotations table.
-- app.py's generate_quotation() INSERT already writes to these
-- columns, but they were never added to quotations (they only
-- exist on the clients table) — causing every POST to
-- /api/quotations/generate to fail with "Unknown column" (seen
-- as a 500 from the frontend).
--
-- Safe to run and re-run: checks information_schema first,
-- same pattern as the other migrations_*.sql files. No DROP
-- statements anywhere in this file.
--
-- Run: mysql -u root -p westlake_insurance < migrations_add_quotation_id_postal.sql
-- Recommended first: mysqldump -u root -p westlake_insurance > backup.sql
-- ============================================================

USE westlake_insurance;

SET @db := DATABASE();

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='quotations' AND COLUMN_NAME='id_number') = 0,
    'ALTER TABLE quotations ADD COLUMN id_number VARCHAR(30) NULL AFTER email',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='quotations' AND COLUMN_NAME='postal_address') = 0,
    'ALTER TABLE quotations ADD COLUMN postal_address VARCHAR(150) NULL AFTER id_number',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;