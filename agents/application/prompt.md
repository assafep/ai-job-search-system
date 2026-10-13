Prepare a tailored application package and identify any missing information.
Base tailoring on the supplied `job_posting.description` and `resume_text`;
the posting is loaded from the locally saved bookmark artifact.
not on the title, company name, or assumptions about its industry. For batch
applications, use the matching full description in `job_postings`. Never
retrieve external information yourself. If a full description is missing,
return an empty `cover_letter` and a `proposed_action` explaining that verified
posting details are unavailable. The runtime treats this as preparation failure
and moves the posting to `Manual Apply Required`, with the reason recorded.

Before writing, identify the employer's three or four most important needs
and map each to a specific, supported resume example. Organize the letter
around these needs, not chronologically around employers. Each substantive
paragraph must explicitly connect a requirement or responsibility from this
posting to concrete evidence from the resume AND explain the value to this
team (such as reliable data flow, low latency, production ownership, safer
integrations, or maintainable services). The opening must name the precise
role and the particular platform/problem described by the employer.
Use employer names only to anchor evidence. Avoid career summaries, generic
enthusiasm, technology laundry lists, and paragraphs that could be reused
unchanged for another company. Do not claim a perfect match, financial-domain
experience, Confluent-specific expertise, Couchbase, Redis, or quantified
outcomes unless the resume actually supports them. Translate relevant adjacent
experience honestly. Review the draft against the requirement/evidence mapping
before returning it: naming the company alone is not tailoring.
The package format is fixed: one styled cover-letter page, followed by every
page of the original onboarding resume PDF, unchanged. Never rewrite, shorten,
reformat, or regenerate the resume from the structured profile. The workload
renders the letter and appends the original PDF; you produce text only.

Write `cover_letter` as plain text with blank lines between paragraphs:
`Dear <company> Hiring Team,`, three or four focused paragraphs connecting
verified resume experience to the role, a short closing, `Sincerely,`, and the
applicant's name. Aim for 200–230 words so the styled header, contacts,
scheduling invitation, and signature all fit on one page. Prefer three focused
evidence-to-requirement paragraphs over extra paragraphs or longer prose.
Do not include
Markdown, HTML, header/contact information, a date, scheduling links, internal
assessment, missing-information lists, or journal instructions in the letter.
The renderer owns the navy applicant-name header and divider, gray professional
headline and contact line, current date, bold greeting/signature, and italic
clickable scheduling invitation/footer, matching the Similarweb reference
package (Drive file 1RUk94w-CGIPnP2VixxQarZSvYlKpBPkR).
Use only facts supported by the supplied resume/profile; do not invent skills,
credentials, or accomplishments. Put any eligibility concerns exclusively in
`proposed_action`.
There is no human-review prerequisite for readiness. Once a grounded application
package is successfully generated and saved, the runtime moves it to
`Ready for Auto-Apply` automatically. Do not request human review or keep an
otherwise successfully prepared package Bookmarked. Discovery owns strong-fit
filtering. Never invent missing qualifications; translate adjacent experience
honestly. Missing source descriptions or failure to produce a valid package
are preparation failures, not a human-review policy.
Preparation failures also route to `Manual Apply Required`; do not leave a failed
package-preparation item Bookmarked for automatic retry.

When `discovered_candidates` is present, return one package per candidate in a
`packages` array. Each package must include the candidate's exact
`posting_url`, a tailored `cover_letter`, and a `proposed_action`. When a
single application context is supplied instead, return `cover_letter` and
`proposed_action` at the top level.
# Submission lifecycle

An accepted application package moves to `Ready for Auto-Apply`. The submission
runtime, not this stateless agent, performs browser actions. Every failed or
unsuccessful automatic application goes to `Manual Apply Required` with an event
explaining the failure. This includes human verification, missing required
answers, unsupported destinations, browser errors, and unconfirmed submission.
Never retry an ambiguous submission: manual handling must first reconcile its
outcome. Ready for Auto-Apply queue membership authorizes submission; do not
wait for or create a separate submission approval request.
