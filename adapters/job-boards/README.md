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
