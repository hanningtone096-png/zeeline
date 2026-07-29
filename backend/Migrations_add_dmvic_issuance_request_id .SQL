-- Adds a column to store DMVIC's IssuanceRequestID, returned when a
-- certificate issuance triggers a "policy alert" (e.g. double insurance)
-- and must be finalized via the Confirm Certificate Issuance API instead
-- of being issued outright. See dmvic_confirm_certificate_issuance() in
-- app.py and the /api/dmvic/confirm-issuance admin route.

SET @db = DATABASE();

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND COLUMN_NAME='dmvic_issuance_request_id') = 0,
    'ALTER TABLE policies ADD COLUMN dmvic_issuance_request_id VARCHAR(64) NULL DEFAULT NULL
        COMMENT ''DMVIC IssuanceRequestID for a pending policy-alert confirmation''',
    'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA=@db AND TABLE_NAME='policies' AND INDEX_NAME='idx_policies_dmvic_issuance_request_id') = 0,
    'CREATE INDEX idx_policies_dmvic_issuance_request_id ON policies (dmvic_issuance_request_id)', 'SELECT 1'
));
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;