-- dialect: postgresql
-- Initial AJSS PostgreSQL schema. Provider commands and credentials belong in
-- onboarding adapters. Existing IDs and historical timestamps survive imports.
CREATE TABLE IF NOT EXISTS public.applications (
    application_id text PRIMARY KEY,
    company text NOT NULL,
    role text NOT NULL,
    posting_url text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'Bookmarked',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_action text,
    next_action_at timestamptz,
    source_document_id text,
    source_record_id text,
    heading text,
    terminal_status text,
    completed_on date,
    UNIQUE (source_document_id, source_record_id)
);

CREATE INDEX IF NOT EXISTS applications_status_idx ON public.applications (status);

CREATE TABLE IF NOT EXISTS public.events (
    event_id text PRIMARY KEY,
    application_id text NOT NULL REFERENCES public.applications (application_id),
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    occurred_on date,
    position integer,
    body text,
    source_document_id text,
    data jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS events_application_idx
    ON public.events (application_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS public.artifacts (
    artifact_id text PRIMARY KEY,
    application_id text NOT NULL REFERENCES public.applications (application_id),
    name text NOT NULL,
    drive_file_id text,
    mime_type text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS artifacts_application_idx ON public.artifacts (application_id);

-- Several job-board URLs can name one application.
-- company_role is one key, so a different board still counts as already applied.
CREATE TABLE IF NOT EXISTS public.posting_keys (
    application_id text NOT NULL REFERENCES public.applications (application_id),
    key_type text NOT NULL CHECK (key_type IN ('url', 'company_role')),
    lookup_key text NOT NULL,
    raw_value text NOT NULL,
    PRIMARY KEY (key_type, lookup_key)
);

CREATE INDEX IF NOT EXISTS posting_keys_application_idx
    ON public.posting_keys (application_id);

CREATE TABLE IF NOT EXISTS public.event_attributes (
    event_id text NOT NULL REFERENCES public.events (event_id),
    name text NOT NULL,
    value text NOT NULL,
    PRIMARY KEY (event_id, name, value)
);

CREATE OR REPLACE VIEW public.completed_postings AS
SELECT
    k.key_type,
    k.lookup_key,
    k.raw_value,
    a.application_id,
    a.company,
    a.role,
    a.terminal_status,
    a.completed_on,
    a.status
FROM public.posting_keys AS k
JOIN public.applications AS a USING (application_id)
WHERE a.completed_on IS NOT NULL
   OR a.status IN ('Rejected', 'Invalidated');
