-- Run once before deploying the admin-only M-Pesa amount override code.
ALTER TABLE payments
    ADD COLUMN admin_override TINYINT(1) NOT NULL DEFAULT 0,
    ADD COLUMN override_reason VARCHAR(500) NULL;
