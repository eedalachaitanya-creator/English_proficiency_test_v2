@'
-- ===========================================================================
-- Migration: add tab_switches_count, tab_switches_total_seconds to invitations
-- Run against eng_prof_test database in pgAdmin (or psql).
-- Safe to run multiple times - uses ADD COLUMN IF NOT EXISTS.
-- ===========================================================================

BEGIN;

ALTER TABLE invitations
    ADD COLUMN IF NOT EXISTS tab_switches_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tab_switches_total_seconds INTEGER NOT NULL DEFAULT 0;

COMMIT;
'@ | Set-Content migrations\add_tab_switches.sql