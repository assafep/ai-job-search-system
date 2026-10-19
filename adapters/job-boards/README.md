# Job-board adapters

Read-only discovery adapters for job sources. Submission remains behind the
application workflow and approval policy.

## Greenhouse

The Greenhouse Job Board API exposes public postings without an API key. The
adapter is available as `adapters.job_boards.greenhouse` and accepts a JSON
object mapping board tokens to company names:

```bash
export GREENHOUSE_BOARDS='{"acme":"Acme Corp","example":"Example Systems"}'
```

The board token is the company-specific value in its Greenhouse jobs URL. The
adapter returns normalized postings with title, company, location, URL,
description, departments, offices, source ID, and update time. It never
submits applications or mutates persistence state.

Scheduled discovery supports `source_names: ["greenhouse"]` and defaults to
the verified employer tokens in `config/discovery-sources.json` when
`GREENHOUSE_BOARDS` is unset. Live employer results are filtered to the profile's
countries and relevant engineering titles/skills before entering Discovered.
Known publication dates older than 30 days are excluded. `first_published`
is retained as `posted_at`, separately from `updated_at`: a recent edit does
not make an old advertisement new. Live jobs with no publication timestamp
are labeled `live_age_unknown`, never claimed to be recently posted. An
unspecified remote/EMEA location does not establish eligibility from Israel.
Nebius is a verified board: its Tel Aviv Senior Data Engineer and Senior
Software Engineer roles were first published within 30 days and name Python.

## Ashby

Use `source_names: ["ashby"]` for the public employer boards in
`config/discovery-sources.json`. Only jobs with `isListed: true` are discovered.
Primary and secondary locations contribute to country eligibility. `publishedAt`
means last publication, so the adapter records `publication_date_kind:
last_published`; it does not claim to know the first publication date.
The same 30-day publication window and profile filters as Greenhouse apply.
An individual employer failure is reported while healthy boards still run.

## Comeet

Comeet is the common careers site for Israeli tech employers. Scheduled
discovery uses `source_names: ["comeet"]` and the company ids in
`config/discovery-sources.json` (`slug/uid` or a bare uid). The adapter reads
the public careers token from the company board, then the Careers API. It
keeps published, non-internal positions. `time_updated` is stored as
`updated_at` only and is not a publication date. Positions last edited more
than 60 days ago are left out. Matching jobs that were edited inside that
window stay labeled `live_age_unknown`. Country codes on the position decide
Israel eligibility. The same role, skill, and profile-country filters used for
Greenhouse then apply. Stream.Security is a verified board: its Ramat Gan
Software Engineer (Modeling) role names Python. Comeet has no publication
date; that position was last edited on 2026-10-18, inside the 60-day window.

## Lever

`source_names: ["lever"]` reads the public postings API for the employer
tokens in `config/discovery-sources.json`. `createdAt` is the publication
time. A missing global board is tried once on the EU host. Israel eligibility
comes from Lever's country code and from the location text. Lendbuzz is a
verified board: its Tel Aviv Backend Engineer was created on 2026-10-01 and
names Python and Microservices Architecture. Mobileye is on the EU host:
Jerusalem Production Software Engineer for Algo Objects was created on
2026-10-18 and names Python.

## SmartRecruiters

`source_names: ["smartrecruiters"]` reads the public postings API for the
company identifiers in `config/discovery-sources.json`. `releasedDate` is the
publication time. The list response has no description, so the adapter fetches
a posting detail only when the list location is Israel (country code or an
Israel city, including Hebrew names). The same 30-day, role, and profile-skill
filters then apply. An individual employer failure is reported while healthy
boards still run.

## Himalayas

`source_names: ["himalayas"]` searches the public Himalayas jobs API for the
profile's first preferred country. `exclude_worldwide` is always set, so a
worldwide remote role is not treated as an Israel role. A restriction list
longer than six countries is treated the same way. Results use the
Himalayas application link. Publication time is `pubDate`. The same 30-day,
role, and profile-skill filters then apply. Search terms come from the
profile, with backend, Java, Scala, Python, and data engineer as the fallback.

## Adzuna

Adzuna provides broad job-search results without requiring a company list. Set
the credentials from an Adzuna developer account and choose the country code:

```bash
export ADZUNA_APP_ID='...'
export ADZUNA_APP_KEY='...'
export ADZUNA_COUNTRY='il'
```

`adapters.job_boards.adzuna` returns the same normalized posting shape as the
other discovery adapters. It only performs read-only searches.

## Freehire (resume-aware discovery)

Freehire provides public, unauthenticated search over indexed company career
pages. Use `source_names: ["freehire"]`; the runtime defaults to Israel
(`countries=IL`) and sends a broad result set to the AI, which ranks it against
the applicant profile. No search terms or credentials are required.

## Jobicy (default free source)

Jobicy's public remote-jobs endpoint requires no account, API key, or payment.
Use `source_names: ["jobicy"]` in a discovery event. The free response keeps
the Jobicy listing URL; the paid direct-ATS option is not used.
