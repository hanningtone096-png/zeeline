-- ============================================================
-- WESTLAKE INSURANCE — migrations_add_commissions.sql
-- Flat-% commission tracking + payout records.
--
-- Commission earned per policy is computed as
--     policies.total_payable * commission_rate_percent / 100
-- for ACTIVE policies (i.e. fully paid / activated). Nothing per-policy
-- is stored; only the rate (app_settings) and disbursed payouts
-- (commission_payouts) are persisted. "Pending payout" for an agent is
--   lifetime_earned  -  SUM(commission_payouts.amount WHERE status='paid')
--
-- Run: mysql -u root -p westlake_insurance < migrations_add_commissions.sql
-- ============================================================

-- Generic key/value settings (reusable beyond commission).
CREATE TABLE IF NOT EXISTS app_settings (
    `key`       VARCHAR(60) PRIMARY KEY,
    `value`     VARCHAR(255) NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Default flat commission rate: 10%. Admin can change it via the UI.
INSERT IGNORE INTO app_settings (`key`, `value`)
VALUES ('commission_rate_percent', '10');

-- Payouts disbursed to agents. `amount` is the commission paid out for the
-- covered period; `status` tracks pending vs paid.
CREATE TABLE IF NOT EXISTS commission_payouts (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    agent_id      INT NOT NULL,
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    amount        DECIMAL(12,2) NOT NULL,
    status        ENUM('pending','paid') NOT NULL DEFAULT 'paid',
    paid_at       TIMESTAMP NULL,
    created_by    INT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id)  REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),
    INDEX idx_agent_status (agent_id, status),
    INDEX idx_period        (period_start, period_end)
) ENGINE=InnoDB;