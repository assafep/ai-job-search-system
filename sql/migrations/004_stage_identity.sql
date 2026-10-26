-- dialect: postgresql
-- Add canonical stage identity without changing legacy requests or registry keys.
-- Data only: no DDL or object ownership needed. Safe before or after code deploy.
DO $migration$
DECLARE
    original jsonb;
    migrated jsonb;
BEGIN
    -- Same order as native-browser execution: execution guard, then run store.
    PERFORM set_config('lock_timeout', '15s', true);
    PERFORM pg_advisory_xact_lock(hashtextextended('workflow-execution', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('workflow-runs', 0));
    SELECT data INTO original FROM public.installation_resources
        WHERE resource_key = 'workflow_runs' FOR UPDATE;
    IF original IS NULL THEN
        RETURN;
    END IF;
    IF jsonb_typeof(original) <> 'object' THEN
        RAISE EXCEPTION 'workflow_runs must be an object';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_each(original) AS r
        WHERE jsonb_typeof(r.value) IS DISTINCT FROM 'object'
           OR jsonb_typeof(r.value #> '{request,workflow}') IS DISTINCT FROM 'string'
           OR btrim(r.value #>> '{request,workflow}') = ''
           OR (r.value ? 'stage' AND r.value->'stage' IS DISTINCT FROM r.value #> '{request,workflow}')
    ) THEN
        RAISE EXCEPTION 'Invalid or conflicting stage identity; migration aborted';
    END IF;
    SELECT coalesce(jsonb_object_agg(r.key, r.value || jsonb_build_object(
        'stage', r.value #> '{request,workflow}')), '{}'::jsonb)
        INTO migrated FROM jsonb_each(original) AS r;
    IF migrated IS DISTINCT FROM original THEN
        UPDATE public.installation_resources SET data = migrated
            WHERE resource_key = 'workflow_runs';
    END IF;
    -- IDs, original requests, claims, statuses, reports and browser tasks survive.
    IF EXISTS (
        SELECT 1 FROM jsonb_each(original) AS r
        WHERE ((migrated->r.key) - 'stage') IS DISTINCT FROM (r.value - 'stage')
    ) THEN
        RAISE EXCEPTION 'Migration changed fields other than stage';
    END IF;
END
$migration$;
