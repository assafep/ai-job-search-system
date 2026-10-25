-- dialect: postgresql
-- Runtime coordination and complete archive snapshots; no provider dependencies.
CREATE TABLE IF NOT EXISTS public.installation_resources (
    resource_key text PRIMARY KEY,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.completed_snapshots (
    application_id text PRIMARY KEY REFERENCES public.applications(application_id),
    snapshot jsonb NOT NULL
);
