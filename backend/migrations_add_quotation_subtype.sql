-- Adds sub_type to the quotations table.
--
-- Needed so that sub_type (e.g. 'own_goods' / 'general_cartage' / 'prime_mover'
-- for Definite's commercial_hybrid product, or 'scheme' / 'individual' for
-- motorcycle_psv rating) survives from quote generation through to
-- issue_dmvic_certificate() later — previously it was only used in-memory for
-- premium calculation and was never persisted, so DMVIC Type B issuance had no
-- way to resolve commercial_hybrid into a VehicleType and was held as
-- 'pending_manual' every time.
--
-- Run this once against your existing database:
--   mysql -u root -p westlake_insurance < migrations_add_quotation_subtype.sql

ALTER TABLE quotations
    ADD COLUMN sub_type VARCHAR(50) NULL AFTER product;