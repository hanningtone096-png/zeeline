-- ============================================================
-- WESTLAKE INSURANCE MANAGEMENT SYSTEM — v2.0 Database Schema
-- MySQL 8.0+
-- Run: mysql -u root westlake_insurance < schema.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS westlake_insurance
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE westlake_insurance;

-- ── Users (agents + admin) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    full_name     VARCHAR(120) NOT NULL,
    username      VARCHAR(60)  NOT NULL UNIQUE,
    email         VARCHAR(120) UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    role          ENUM('admin','agent') NOT NULL DEFAULT 'agent',
    status        ENUM('pending','approved','rejected','suspended') NOT NULL DEFAULT 'pending',
    phone         VARCHAR(20),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Default admin (password: Admin@2024 — change immediately)
INSERT IGNORE INTO users (full_name, username, email, password_hash, role, status)
VALUES (
    'System Administrator',
    'admin',
    'admin@westlakeinsurance.co.ke',
    'pbkdf2:sha256:600000$westlake$a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
    'admin',
    'approved'
);

-- ── Clients ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    agent_id     INT NOT NULL,
    first_name   VARCHAR(80) NOT NULL,
    last_name    VARCHAR(80) NOT NULL,
    phone        VARCHAR(20) NOT NULL,
    email        VARCHAR(120),
    id_number    VARCHAR(30),
    kra_pin      VARCHAR(20),
    vehicle_reg  VARCHAR(20),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_agent      (agent_id),
    INDEX idx_phone      (phone),
    INDEX idx_vehicle    (vehicle_reg),
    INDEX idx_id_number  (id_number),   -- National ID lookups
    INDEX idx_email      (email)
) ENGINE=InnoDB;

-- ── Quotations ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quotations (
    id                    VARCHAR(40) PRIMARY KEY,   -- WL-YYYYMMDD-XXXXXX
    agent_id              INT NOT NULL,
    company               VARCHAR(60) NOT NULL,
    type_of_cover         VARCHAR(40) NOT NULL,
    type_of_certificate   VARCHAR(20) NOT NULL,
    product               VARCHAR(30) NOT NULL,
    commencing_date       DATE NOT NULL,
    expiry_date           DATE NOT NULL,
    -- Insured
    policy_holder_name    VARCHAR(120) NOT NULL,
    kra_pin               VARCHAR(20),
    phone                 VARCHAR(20) NOT NULL,
    email                 VARCHAR(120),
    -- Vehicle
    vehicle_reg           VARCHAR(20) NOT NULL,
    chassis_number        VARCHAR(40),
    engine_number         VARCHAR(40),
    vehicle_body_type     VARCHAR(60),
    make                  VARCHAR(80),
    seats                 TINYINT UNSIGNED DEFAULT 1,
    vehicle_value         DECIMAL(14,2) DEFAULT 0,
    tonnage               DECIMAL(8,2),
    -- Financials
    base_premium          DECIMAL(12,2) NOT NULL,
    levies_and_taxes      DECIMAL(12,2) NOT NULL,
    total_payable         DECIMAL(12,2) NOT NULL,
    -- Status
    status                ENUM('pending','converted','cancelled') DEFAULT 'pending',
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES users(id),
    INDEX idx_agent   (agent_id),
    INDEX idx_vehicle (vehicle_reg),
    INDEX idx_date    (created_at),
    INDEX idx_phone   (phone),
    INDEX idx_email   (email)
) ENGINE=InnoDB;

-- ── Policies ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policies (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    policy_no        VARCHAR(40) NOT NULL UNIQUE,
    quote_id         VARCHAR(40),
    agent_id         INT NOT NULL,
    client_id        INT NOT NULL,
    vehicle_reg      VARCHAR(20),
    type_of_cover    VARCHAR(40),
    commencing_date  DATE,
    expiry_date      DATE NOT NULL,
    total_payable    DECIMAL(12,2),
    status           ENUM('active','expired','cancelled','suspended') DEFAULT 'active',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id)  REFERENCES users(id),
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (quote_id)  REFERENCES quotations(id),
    INDEX idx_expiry  (expiry_date),
    INDEX idx_agent   (agent_id),
    INDEX idx_client  (client_id),
    INDEX idx_vehicle (vehicle_reg),
    INDEX idx_status  (status)
) ENGINE=InnoDB;

-- Auto-expire policies
CREATE EVENT IF NOT EXISTS expire_policies
ON SCHEDULE EVERY 1 DAY
STARTS (TIMESTAMP(CURRENT_DATE))
DO
    UPDATE policies SET status='expired'
    WHERE  expiry_date < CURDATE() AND status='active';

-- ── Payments ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    policy_no   VARCHAR(40) NOT NULL,
    amount      DECIMAL(12,2) NOT NULL,
    status      ENUM('pending','completed','failed','refunded') DEFAULT 'pending',
    method      ENUM('mpesa','bank','cash','manual') DEFAULT 'manual',
    reference   VARCHAR(60),
    paid_at     TIMESTAMP NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (policy_no) REFERENCES policies(policy_no),
    INDEX idx_policy (policy_no)
) ENGINE=InnoDB;

-- ── Claims ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS claims (
    id            VARCHAR(20) PRIMARY KEY,
    agent_id      INT NOT NULL,
    claim_policy  VARCHAR(20) NOT NULL,
    incident_date DATETIME NOT NULL,
    incident_type VARCHAR(40) NOT NULL,
    incident_desc TEXT NOT NULL,
    status        ENUM('pending','approved','rejected') DEFAULT 'pending',
    notes         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES users(id),
    INDEX idx_policy (claim_policy),
    INDEX idx_agent  (agent_id)
) ENGINE=InnoDB;

-- ── Documents ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    client_id  INT,
    claim_id   VARCHAR(20),
    quote_id   VARCHAR(40),
    filename   VARCHAR(255) NOT NULL,
    filepath   VARCHAR(512) NOT NULL,
    doc_type   ENUM('kra_certificate','national_id','claim_evidence',
                    'policy_document','other') DEFAULT 'other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_user   (user_id),
    INDEX idx_client (client_id),
    INDEX idx_claim  (claim_id)
) ENGINE=InnoDB;

-- ── Audit Log ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT,
    action     VARCHAR(80) NOT NULL,
    detail     TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user   (user_id),
    INDEX idx_action (action),
    INDEX idx_time   (created_at)
) ENGINE=InnoDB;

-- ── Reports cache (optional) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    period     ENUM('daily','weekly','monthly','yearly') NOT NULL,
    start_date DATE NOT NULL,
    end_date   DATE NOT NULL,
    summary    JSON,
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
) ENGINE=InnoDB;

-- ── Enable event scheduler ────────────────────────────────────────────────────
SET GLOBAL event_scheduler = ON;