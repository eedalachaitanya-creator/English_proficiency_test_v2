@'
-- ===========================================================================
-- Migration: add access_code, failed_code_attempts, code_locked to invitations
-- Run against eng_prof_test database in pgAdmin (or psql).
-- Safe to run multiple times - uses ADD COLUMN IF NOT EXISTS.
-- ===========================================================================

BEGIN;

-- Step 1: add columns nullable so existing rows do not violate NOT NULL
ALTER TABLE invitations
    ADD COLUMN IF NOT EXISTS access_code VARCHAR(6),
    ADD COLUMN IF NOT EXISTS failed_code_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS code_locked BOOLEAN NOT NULL DEFAULT FALSE;

-- Step 2: backfill existing rows with random 6-digit codes
UPDATE invitations
   SET access_code = LPAD((FLOOR(RANDOM() * 1000000))::TEXT, 6, '0')
 WHERE access_code IS NULL;

-- Step 3: enforce NOT NULL now that all rows have a value
ALTER TABLE invitations
    ALTER COLUMN access_code SET NOT NULL;

COMMIT;
'@ | Set-Content migrations\add_access_code.sql