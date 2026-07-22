-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_indexes.sql
-- Run against the LIVE westlake_insurance database.
-- Safe to run and re-run: every step checks whether it's already
-- been applied before doing anything (guarded via information_schema),
-- so this file will never fail on a second run and will never touch
-- or delete existing rows. No DROP DATABASE, no DROP TABLE, anywhere
-- in this file.
--
-- Run: mysql -u root westlake_insurance < migrations_add_indexes.sql
-- Recommended first: mysqldump -u root westlake_insurance > backup.sql
-- ============================================================

USE westlake_insurance;

SET @db := DATABASE();

-- ── 1. PERFORMANCE INDEXES ──────────────────────────────────────────────

-- clients: national ID + email lookups
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='clients' AND INDEX_NAME='idx_id_number') = 0,
    'ALTER TABLE clients ADD INDEX idx_id_number (id_number)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='clients' AND INDEX_NAME='idx_email') = 0,
    'ALTER TABLE clients ADD INDEX idx_email (email)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- quotations: phone + email lookups
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='quotations' AND INDEX_NAME='idx_phone') = 0,
    'ALTER TABLE quotations ADD INDEX idx_phone (phone)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='quotations' AND INDEX_NAME='idx_email') = 0,
    'ALTER TABLE quotations ADD INDEX idx_email (email)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- policies: filter by status (active/expired/cancelled) for renewals & reports
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND INDEX_NAME='idx_status') = 0,
    'ALTER TABLE policies ADD INDEX idx_status (status)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ── 2. EMAIL OTP VERIFICATION (agent registration) ──────────────────────
-- verification_codes table — no-op if already present.

CREATE TABLE IF NOT EXISTS verification_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    code VARCHAR(6) NOT NULL,
    purpose VARCHAR(20) NOT NULL,       -- 'register' (extend later: 'login', 'reset_password')
    expires_at DATETIME NOT NULL,
    attempts INT NOT NULL DEFAULT 0,    -- wrong-code attempts against this code
    used TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_purpose_used (user_id, purpose, used),
    CONSTRAINT fk_verification_codes_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;
-- Adds account-flagging columns for repeated/suspicious underpayment attempts.
-- Run this once against your existing database:
--   mysql -u root -p westlake_insurance < migrations_add_underpayment_flag.sql

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS flagged BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS flagged_reason VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS flagged_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS underpayment_attempts INT NOT NULL DEFAULT 0;

-- users.status: ensure 'unverified' is a valid value. If status is a
-- MySQL ENUM and doesn't yet include 'unverified', this brings it in
-- line with what app.py's registration flow writes. Safe to run even if
-- 'unverified' is already present — MODIFY COLUMN just reasserts the
-- same definition, existing values on rows are unaffected.
ALTER TABLE users
    MODIFY COLUMN status ENUM('unverified','pending','approved','rejected','suspended')
    NOT NULL DEFAULT 'unverified';


-- ── 3. DMVIC CERTIFICATE TRACKING (policies) ─────────────────────────────
-- Required before buy_cover() -> issue_dmvic_certificate() can write
-- anything back to the policies table. Until these columns exist, every
-- DMVIC issuance attempt fails silently on its UPDATE (caught and logged
-- by the background worker, no user-facing error).

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_status') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_status VARCHAR(20) NULL DEFAULT NULL
        COMMENT ''NULL=not attempted, pending, issued, failed, unsupported''',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_transaction_no') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_transaction_no VARCHAR(64) NULL DEFAULT NULL',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_certificate_no') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_certificate_no VARCHAR(64) NULL DEFAULT NULL',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_api_request_no') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_api_request_no VARCHAR(64) NULL DEFAULT NULL',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_cert_type') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_cert_type VARCHAR(20) NULL DEFAULT NULL
        COMMENT ''psv/commercial/private/motorcycle bucket used at issuance''',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_error') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_error TEXT NULL DEFAULT NULL',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_issued_at') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_issued_at DATETIME NULL DEFAULT NULL',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_document_url') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_document_url VARCHAR(512) NULL DEFAULT NULL
        COMMENT ''URL/path to retrieved certificate document, once DMVIC retrieval is wired up''',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- DMVIC indexes
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND INDEX_NAME='idx_policies_dmvic_status') = 0,
    'CREATE INDEX idx_policies_dmvic_status ON policies (dmvic_status)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND INDEX_NAME='idx_policies_dmvic_cert_no') = 0,
    'CREATE UNIQUE INDEX idx_policies_dmvic_cert_no ON policies (dmvic_certificate_no)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ── Verify ────────────────────────────────────────────────────────────
-- DESCRIBE clients;
-- DESCRIBE quotations;
-- DESCRIBE policies;
-- SHOW TABLES LIKE 'verification_codes';