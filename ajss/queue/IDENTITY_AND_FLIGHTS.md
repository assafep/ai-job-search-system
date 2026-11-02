# Posting identities and database flights

PostgreSQL lookup/reservation checks `gdtq_identities` and historical
`posting_keys` in database `ajss-db`. Reservation locks keys in sorted order.
Both backends use `ajss.queue.identity`; SQLite also accepts its previous keys.

URL rules: trim outer whitespace, lowercase scheme/host, remove fragments
and trailing path slashes (preserve `/` at the root). Remove only utm_source,
utm_medium, utm_campaign, utm_term, utm_content, fbclid and gclid. Preserve
other query parameters, blank values and query order. Preserve path case,
HTTP versus HTTPS, and www; do not infer redirects or interchangeable job IDs.
Company/role rules: collapse whitespace, trim and Unicode casefold each value,
then join with ` / `. Punctuation, company suffixes and role synonyms remain.
This is exact matching under these transformations, not fuzzy matching.

Bookmarking reserves/checks supplied identity before fetching, then rechecks
verified company/role and resolved URL after extraction. Any encountered
identity counts, regardless of terminal status. A company-role key may cover
multiple distinct vacancies; this intentionally preserves existing skip policy.
Historical database keys remain unchanged; no live migration was performed.

Set `AJSS_FLIGHT_STORAGE=database` to keep flight claims in `gdtq_items`
while application tabs stay under their stable state until completion. Default
`document` preserves existing installations. Provisioning in database mode
creates only stable state roots. Stop the existing writer and reconcile existing
onFlight items before switching. Existing flight tabs are not automatically
moved/deleted. Never switch modes with unresolved attempts.

Expired claims are observable through `expired_claims()` but are not automatically
reassigned in either mode. Verify external effects and reconcile the specific
attempt before releasing it, especially for submission. Outcomes must own their
claim. Document event append and state move share a revision-checked batch;
claim release follows durable event logging. If either system fails, retain the
claim for reconciliation. Google Docs and PostgreSQL do not share a transaction.
Continue to run only one document writer.

Restored prompt contracts are callable through `ajss.documents.prompts.run` with an
explicit provider; default worker scoring remains deterministic. Live Google,
Gmail and browser integration requires separate nonproduction validation.
