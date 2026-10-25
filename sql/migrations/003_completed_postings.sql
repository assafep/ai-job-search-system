-- dialect: postgresql
-- One completed application can be known by several job-board URLs.
-- The same normalized company and role is the same application.
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS heading text;
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS terminal_status text;
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS completed_on date;

ALTER TABLE public.events ADD COLUMN IF NOT EXISTS occurred_on date;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS position integer;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS body text;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS source_document_id text;

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

-- Answer "was this posting already processed to completion?"
-- Pass a normalized URL, or a normalized "company / role" key.
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
