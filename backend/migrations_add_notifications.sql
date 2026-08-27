-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_notifications.sql
-- In-app notification ledger for the header bell dropdown.
--
-- Scope model:
--   user_id IS NULL  => admin-only event (only admins see it)
--   user_id = <agent> => that agent's notification (admins see it too,
--                        because admins query all rows)
--
-- Run: mysql -u root -p westlake_insurance < migrations_add_notifications.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS notifications (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NULL,                 -- NULL = admin-only; else the agent it targets
    type        VARCHAR(40)  NOT NULL,    -- agent_pending | dmvic_flag | policy_expiring | mpesa_failed
    title       VARCHAR(120) NOT NULL,
    message     VARCHAR(255) NOT NULL,
    link        VARCHAR(160),             -- app route the bell item opens
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_read (user_id, is_read),
    INDEX idx_created    (created_at)
) ENGINE=InnoDB;