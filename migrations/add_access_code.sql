-- ===========================================================================
-- Migration: add access_code, failed_code_attempts, code_locked to invitations
-- Run against eng_prof_test database in pgAdmin (or psql).
-- Safe to run multiple times - uses ADD COLUMN IF NOT EXISTS.
-- ===========================================================================

BEGIN;

ALTER TABLE invitations
    ADD COLUMN IF NOT EXISTS access_code VARCHAR(6),
    ADD COLUMN IF NOT EXISTS failed_code_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS code_locked BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE invitations
   SET access_code = LPAD((FLOOR(RANDOM() * 1000000))::TEXT, 6, '0')
 WHERE access_code IS NULL;

ALTER TABLE invitations
    ALTER COLUMN access_code SET NOT NULL;

COMMIT;