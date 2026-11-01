-- Read-only validation; expected 160 historical rows, zero missing,
-- and the existing 240 posting keys must all remain represented.
SELECT count(*) AS historical_applications FROM public.applications;
SELECT count(*) AS new_identity_keys FROM public.gdtq_identities;
SELECT count(*) AS missing_applications FROM public.applications a
WHERE NOT EXISTS (SELECT 1 FROM public.gdtq_identities i WHERE i.application_id=a.application_id);
SELECT count(*) AS lost_old_keys FROM public.posting_keys k WHERE NOT EXISTS(
  SELECT 1 FROM public.gdtq_identities g
  WHERE g.key_type=k.key_type AND g.lookup_key=k.lookup_key AND g.application_id=k.application_id
);
SELECT origin,count(*) FROM public.gdtq_identities GROUP BY origin ORDER BY origin;
-- Re-run the migration SQL and verify counts do not change.
