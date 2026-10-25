# Google Drive adapter

Reads and writes Drive/Docs only through the Application Service. The
Interviews Journal is organized as six top-level state tabs, with application
tabs nested beneath them. Application tabs contain a human-readable event
journal: a date paragraph followed by bulleted notes. Research tabs nested under
an application are preserved and are not treated as applications.

Artifacts are stored under the configured Drive root using one application
folder named:

    <root>/<company_name> / <role_name>/<artifact>

The optional `application_root_path` setting can insert a root-relative parent,
for example `applications/<company_name> / <role_name>`. The default remains
the direct-root layout for compatibility with the existing Drive tree.

Artifact references are appended to the corresponding application journal.
Authentication remains outside local runtime configuration. The repository
loads the journal and artifact-root URLs from `config/runtime.json` created by
onboarding, unless explicit environment-variable overrides are provided.

The applicant profile is stored at `.ai-job-search-system/applicant_profile.json`
under the configured root and is accessed through the Application Service's `/profile`
resource. Previously misnamed files are renamed in place on the next profile write.
