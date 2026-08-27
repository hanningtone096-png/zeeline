-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_renewal_reminders.sql
-- Audit log of every renewal reminder sent, so the daily scheduler
-- never double-sends the same interval (30d/14d/3d) to the same
-- recipient for the same policy.
--
-- Run: mysql -u root -p westlake_insurance < migrations_add_renewal_reminders.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS renewal_reminders (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    policy_no     VARCHAR(40)  NOT NULL,
    recipient     VARCHAR(120) NOT NULL,        -- email (or phone when SMS added)
    channel       ENUM('email','sms') NOT NULL DEFAULT 'email',
    interval_type VARCHAR(10)  NOT NULL,         -- '30d' | '14d' | '3d'
    sent_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (policy_no) REFERENCES policies(policy_no) ON DELETE CASCADE,
    INDEX idx_policy_interval (policy_no, interval_type),
    INDEX idx_sent             (sent_at)
) ENGINE=InnoDB;