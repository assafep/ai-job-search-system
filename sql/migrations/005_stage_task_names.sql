-- dialect: postgresql
-- Breaking data cutover. Stop all execution and deploy the stage/task contract
-- with execution disabled before applying. No application/archive rows change.
DO $migration$
DECLARE
    item record;
    node record;
    payload jsonb;
    part jsonb;
    mappings jsonb := '{"workflow":"stage","run_id":"stage_task_id"}'::jsonb;
    old_key text;
    new_key text;
BEGIN
    PERFORM set_config('lock_timeout', '15s', true);
    -- Include both old and new locks during the one-time cutover. This does
    -- not make simultaneous old/new execution supported: stop workers first.
    PERFORM pg_advisory_xact_lock(hashtextextended('workflow-execution', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('workflow-runs', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('stage-execution', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('stage-tasks', 0));
    IF EXISTS (SELECT 1 FROM public.installation_resources WHERE resource_key = 'workflow_runs')
       AND EXISTS (SELECT 1 FROM public.installation_resources WHERE resource_key = 'stage_tasks') THEN
        RAISE EXCEPTION 'Both request registries exist; refusing to merge execution histories';
    END IF;
    FOR item IN SELECT resource_key, data FROM public.installation_resources
        WHERE resource_key IN ('workflow_runs', 'hosted_workloads', 'hosted_approvals') FOR UPDATE
    LOOP
        payload := item.data;
        -- Visit children before parents so renaming a key cannot invalidate
        -- a child path. Arrays, claims and opaque values are preserved.
        FOR node IN
            WITH RECURSIVE walk(path, value) AS (
                SELECT ARRAY[]::text[], item.data
                UNION ALL
                SELECT walk.path || child.key, child.value FROM walk
                CROSS JOIN LATERAL (
                    SELECT key, value FROM jsonb_each(CASE WHEN jsonb_typeof(walk.value) = 'object'
                        THEN walk.value ELSE '{}'::jsonb END)
                    UNION ALL
                    SELECT (ordinality - 1)::text, value FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(walk.value) = 'array' THEN walk.value ELSE '[]'::jsonb END)
                        WITH ORDINALITY
                ) AS child
            ) SELECT path FROM walk WHERE jsonb_typeof(value) = 'object'
              ORDER BY cardinality(path) DESC
        LOOP
            part := payload #> node.path;
            FOR old_key, new_key IN SELECT key, value FROM jsonb_each_text(mappings)
            LOOP
                IF part ? old_key THEN
                    IF part ? new_key AND part->new_key IS DISTINCT FROM part->old_key THEN
                        RAISE EXCEPTION 'Conflicting identity in resource %', item.resource_key;
                    END IF;
                    part := (part - old_key) || jsonb_build_object(new_key, part->old_key);
                END IF;
            END LOOP;
            IF cardinality(node.path) = 0 THEN
                payload := part;
            ELSE
                payload := jsonb_set(payload, node.path, part, false);
            END IF;
        END LOOP;
        UPDATE public.installation_resources SET
            resource_key = CASE WHEN item.resource_key = 'workflow_runs' THEN 'stage_tasks' ELSE item.resource_key END,
            data = payload
            WHERE resource_key = item.resource_key;
    END LOOP;
    -- Fresh installations also receive an explicit initialized registry.
    INSERT INTO public.installation_resources(resource_key, data) VALUES ('stage_tasks', '{}'::jsonb)
        ON CONFLICT (resource_key) DO NOTHING;
END
$migration$;
