-- GDTQ Neon migration, additive/idempotent; must run on a TEST BRANCH first.
-- No existing applications, posting_keys, or completed_snapshots are modified.
-- Requires role with CREATE on public schema. Database: ajss-db.
BEGIN;

CREATE TABLE IF NOT EXISTS public.gdtq_identities(
  key_type text NOT NULL CHECK (key_type IN ('url','company_role','source')),
  lookup_key text NOT NULL,
  application_id text NOT NULL,
  origin text NOT NULL DEFAULT 'gdtq' CHECK (origin IN ('legacy','backfill','gdtq')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(key_type,lookup_key)
);
CREATE INDEX IF NOT EXISTS gdtq_identities_application_idx
  ON public.gdtq_identities(application_id);

-- The original dedup table's (key_type, lookup_key) format is authoritative.
INSERT INTO public.gdtq_identities(key_type,lookup_key,application_id,origin)
SELECT key_type,lookup_key,application_id,'legacy'
FROM public.posting_keys
ON CONFLICT(key_type,lookup_key) DO NOTHING;

-- Backfill company-role identities for ALL historical records, not just
-- the already indexed or completed_on != NULL subset.
INSERT INTO public.gdtq_identities(key_type,lookup_key,application_id,origin)
SELECT 'company_role',lower(btrim(company))||' / '||lower(btrim(role)),
       application_id,'backfill'
FROM public.applications WHERE btrim(company)<>'' AND btrim(role)<>''
ON CONFLICT(key_type,lookup_key) DO NOTHING;

-- Posting URLs stored in applications were confirmed unique; use their
-- source values to recover missing indices. Canonical tracking removal in
-- runtime handles future occurrences.
INSERT INTO public.gdtq_identities(key_type,lookup_key,application_id,origin)
SELECT 'url',posting_url,application_id,'backfill'
FROM public.applications WHERE btrim(posting_url)<>''
ON CONFLICT(key_type,lookup_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.gdtq_items(
  item_id text PRIMARY KEY,
  source_state text NOT NULL,
  attempt_id text,
  lease_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gdtq_items_expired_idx
  ON public.gdtq_items(lease_until) WHERE attempt_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.gdtq_checkpoints(
  name text PRIMARY KEY,
  value jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.gdtq_event_log(
  event_id text PRIMARY KEY,
  item_id text NOT NULL,
  event_json jsonb NOT NULL,
  inserted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gdtq_event_log_item_idx
  ON public.gdtq_event_log(item_id,inserted_at);

-- Assert all historical applications have a GDTQ identity BEFORE commit.
DO $$
DECLARE missing_count integer;
BEGIN
  SELECT count(*) INTO missing_count FROM public.applications a WHERE NOT EXISTS(
    SELECT 1 FROM public.gdtq_identities i WHERE i.application_id=a.application_id
  );
  IF missing_count<>0 THEN
    RAISE EXCEPTION 'GDTQ backfill incomplete: % applications lack identities',missing_count;
  END IF;
END $$;

COMMIT;
