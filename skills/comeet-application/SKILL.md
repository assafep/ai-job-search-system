---
name: comeet-application
description: Discover, render, inventory, fill, preview, and submit Comeet-hosted job application forms, including employer careers pages, Comeet's public careers API, position-data hydration, cross-origin application iframes, dual PDF uploads, and conservative receipt verification. Use for Comeet URLs or Comeet-derived applicant forms.
---

# Comeet careers pages and application forms

This skill is the **Comeet-specific companion** to
[dynamic-web-form-discovery](../dynamic-web-form-discovery/SKILL.md).
Read the general skill first for network hygiene, browser capability checks,
field inventories, preview-only operation, one-shot submission and evidence
standards. Then apply these Comeet discoveries and selectors.

**Status of evidence:** This guide reflects source code in AJSS
(`adapters/job_boards/comeet.py`, `adapters/browser/comeet.py`,
`adapters/browser/comeet.cjs`) and Comeet's public configuration/API
documentation. It is **not** a claim that every Comeet tenant, including
the Tikalk/Tikal posting, has been manually rendered and tested live.
Selectors are a candidate map; always check the current DOM.

## 1. Recognize Comeet domains and job identity

Common public posting:
```text
https://www.comeet.com/jobs/tikalk/68.00C/senior-backend-engineer/13.532
```

Interpret this as:
- `www.comeet.com`: public hosted jobs UI.
- `tikalk`: employer slug (do not equate it blindly with display name).
- `68.00C`: public employer/company UID.
- `senior-backend-engineer`: human-readable posting slug.
- `13.532`: position UID.
- `www.comeet.co`: used in the existing AJSS code for careers API
  and a potential embedded application iframe; it is **not** the same
  hostname as the public jobs page.

The employer's public careers board is typically:
```text
https://www.comeet.com/jobs/tikalk/68.00C
```

The final apply destination is the **exact posting URL given by AJSS**,
after confirming it appears on the employer board. Do not replace it with
an unrelated search result, fabricate a slug or submit for a different UID.

When building URLs, preserve the UID's punctuation exactly and use a
standards-compliant URL/query encoder. Check `scheme=https`, expected
hosts, stable position UID, and the visible employer/role before any form
work. A relative link or redirected origin must be verified rather than
assumed trustworthy.

## 2. First discover the employer and position with public read operations

AJSS currently defines:
```python
API_URL = "https://www.comeet.co/careers-api/2.0/company/{}/positions?{}"
PAGE_URL = "https://www.comeet.com/jobs/{}"
```

Its discovery logic:
1. GET the public employer careers page (e.g. the `/jobs/<slug>/<uid>`
   page). The HTML may contain a quoted `company_uid` and `token`
   pair. The token is publicly exposed **read-only careers access** in
   the current implementation. Never invent, enumerate or log tokens.
2. Use the **observed** token and UID in the public careers API request:
   ```text
   GET https://www.comeet.co/careers-api/2.0/company/{company_uid}/positions?token={observed_token}&details=true
   ```
3. Expect a JSON list, not an assumed dict. Select the position by its
   exact `uid` and verify its name, company and returned hosted posting URL.
   Keep only the fields required for describing/reconciling that job.
4. Public `details` records may contain HTML job description fragments,
   department, location, experience level and `time_updated`.
   `time_updated` is an **edit** timestamp, *not* the publication date.
   Do not claim the position was newly posted because it changed.
5. If the expected token is absent, malformed or the API returns a
   non-list/error, stop that read method and move to rendered inspection;
   do not brute-force token values, bypass the site or invent data.

An alternative read path parses an inline JavaScript serialization:
```text
POSITION_DATA = { ...JSON... }
```
AJSS's `_extract_comeet` decodes this with `json.JSONDecoder().raw_decode`
and extracts `custom_fields.details` and `location`. This is useful
for *job description retrieval*, not proof that an application form was
rendered or that particular controls exist.

### Public read request parameters and headers

Use exactly the parameters observed/documented for the API:
`token=<observed-public-careers-token>`, `details=true`.
A normal truthful `User-Agent`, `Accept: application/json`,
and `Accept-Language` are sufficient for public reads.
Use HTTPS, finite timeout, appropriate URL encoding, and back off from
429. Don't add speculative `apply`, `submit`, `render`, `ajax`,
`csrf`, `callback`, or guessed tokens. Do not replay application
submission endpoints from captured network logs.

**Important separation:** The public Careers API is useful for listing
published jobs; it is **not** an authorized application-submission API.

## 3. Render Comeet application UX in a browser

Use ChatGPT native Browser/Computer Use when a live AJSS stage task
requires it. For an explicitly supported workstation workflow, the
project's browser implementation is Node Playwright + Chrome. The
site-specific code is `adapters/browser/comeet.cjs` launched by
`adapters/browser/comeet.py`. Do **not** mix a native-browser task with
a second local Playwright submission attempt.

Recommended discovery order:
1. Open the public employer board **and validate the exact posting
   link**. Search for the desired role and compare its `href` with the
   saved posting URL. Do not require the word "Senior" for every role;
   a separate hard-coded Senior filter in some older adapter versions
   is over-specific.
2. Open the exact posting URL. Record page title, final URL,
   visible description and buttons.
3. Locate the **open-form** control (often `#showApplyForm` in the
   AJSS adapter), ensure it is not the final submission control,
   and click it only in UI exploration.
4. Wait for the application form to load. In the existing adapter, a
   child frame can have a URL under `https://www.comeet.co/` containing
   `/apply`. Discover `page.frames` after the click, then observe
   frame navigation/attachment. Do not assume the iframe loads immediately.
5. Confirm the candidate form frame by accessible form controls and
   `#inputFirstName` or a visible "First name" field; record its
   actual URL and origin. Enumerate **every** form control inside that
   frame. Some tenants use a different embed/layout; do not fabricate
   a frame if none exists.
6. Keep observation bounded (for example, ~20 seconds with explicit
   waits), but prefer waiting for frame/control evidence over fixed sleeps.
   A cross-origin iframe is accessible through Playwright's frame API,
   even though JavaScript executed in the parent page cannot freely
   read its DOM due to browser-origin rules.

Capture XHR/fetch metadata and iframe navigation **before** clicking
the open-form control; the form configuration may be loaded only when
that button is pressed. Track the rendered form, **not** just a previously
cached HTML response.

## 4. Known Comeet control map — **verify in the live form**

These are selector and label candidates derived from the current AJSS
Comeet browser implementation, not a universal contract:

| Meaning | Existing AJSS selector / observed label candidate |
| --- | --- |
| Open application form | `#showApplyForm` |
| First name | `#inputFirstName` |
| Last name | `#inputLastName` |
| Email | `#inputEmail` |
| Phone | `#inputTel` |
| Website / public link | `#inputLink` |
| Résumé file | `#cv`, label "Attach Resume" |
| Cover-letter file | `#coverLetter`, label "Attach Cover Letter" |
| Portfolio | `#portfolio` |
| Personal note | `#inputNote` |
| Final submission | button accessible name "Submit application" in known adapter |

The form can also have **tenant-specific questions**, consent checkboxes,
radio groups, dropdowns, typed dates, hidden controls, custom widgets
or localized button labels. Comeet allows configuration of form questions
and submission button text; inspect those rather than relying on a fixed
list or exact English button name.

Enumerate:
```css
input, textarea, select, button, [role="button"], [role="checkbox"],
[role="radio"], [role="combobox"], [contenteditable]
```

For each form control record:
- current **frame URL**, form identity, label/accessible name and a
  working unique selector;
- `id`, `name`, `type`, `placeholder`, required and
  `aria-required`, disabled and visible state;
- options, validation rules and present value/checked state
  (avoid outputting sensitive values in logs);
- profile mapping, unknown-answer blocker, or optional status.

Use Playwright `frame.get_by_label(...)` /
`frame.get_by_role(...)` first when possible. When IDs are observed,
`frame.locator("#inputEmail")` etc. provide useful anchors.
Do not assume a top-level `page.locator(...)` can see into the frame.
Clicking the initial "Apply" link is **not** final submission.

Re-enumerate once before filling, once after each major group of fields,
and again after upload and validation so dynamically added controls
aren't missed.

## 5. Profile mappings and answers

The current AJSS Python adapter hands the browser:
```text
context["application"]                  # company, role, posting_url
context["applicant_profile"]            # profile.personal etc.
context["package_bytes"]                 # validated PDF bytes
context["package"]["name"]             # PDF filename
```

The browser derives:
```text
personal.name / personal.full_name
personal.first_name / personal.last_name
personal.email
personal.phone / personal.phone_number
personal.website / personal.website_url
```

Use the saved profile, not a model-inferred identity. Split a full name
only when explicit first/last fields are unavailable; do not infer a
phone number. The model may propose answers to additional questions,
but it must not invent personal history or answer unsupported questions.
Before filling email, verify it matches the applicant profile and
read the rendered field label; a visible email in a screenshot is not
a submission receipt.

Populate through Playwright browser interactions such as `fill`,
`select_option`, `check` and `set_input_files`. After each group
inspect field validation and any unexpected newly required questions.
A missing phone or required answer is a pre-click blocker.

## 6. Crucial AJSS file-upload policy

**The same saved combined résumé + cover-letter PDF intentionally goes
into BOTH Comeet file fields when both exist.** This policy was
explicitly confirmed by the AJSS owner; **do not 'correct' it** by
using only the résumé slot or fabricating a separate cover-letter PDF.

The existing interaction pattern:
1. Identify the `#cv` input and its visual "Attach Resume" trigger.
2. Click its associated trigger / use a file chooser (or use
   `set_input_files` directly if supported).
3. Set the **same package PDF**.
4. Verify the input's FileList includes the expected basename and that
   the UI acknowledges the upload.
5. Repeat for `#coverLetter` / "Attach Cover Letter", using **that
   identical combined PDF**.
6. Recheck both inputs after changes and save the two accepted-attachment
   observations in the form inventory.

If a slot is missing, disabled, accepts incompatible file types, or
the chosen file is rejected, record the actual condition; never claim
that it was uploaded merely because the filename was available locally.

Do not upload the résumé to unknown extra file controls automatically.

## 7. Identify final Submit/Application button

Comeet may distinguish:
- job-board "Apply" link (navigates to posting),
- job-detail `#showApplyForm` button (opens the form),
- **form iframe** final "Submit application" button, possibly
  customized or localized.

Locate the final button *inside the confirmed application form frame*.
The existing code's candidate is:
```javascript
const submit = frame.getByRole('button', { name: /^submit application$/i });
```
But this exact string is **not guaranteed** across tenants. Inspect the
actual accessible name, button type, enclosing form, click semantics and
visible text. Require exactly one actionable final control; fail closed
when ambiguous.

**Preview-only:** fill, verify both attachments, scroll, hover,
take the form screenshot, and stop. Do not call final `click()`.
A screenshot generally omits the native mouse pointer, so optionally
add a clearly annotated hover indicator for a human reviewer.

**Submission:** only after AJSS has durably begun the application
attempt according to the active execution contract, click **once**.
Never use direct network POST to bypass the UI, CAPTCHA or validation.

## 8. Confirmation and uncertainty

Watch the **form frame** as well as top-level page for:
- explicit application received / submitted / thank-you text;
- confirmed redirect and/or visible application reference;
- human verification messages, validation errors or unchanged form.

AJSS's existing Comeet browser code searches for English expressions
such as "application ... received" and "thank you for applying".
These are *examples*, not a universal receipt detector. Avoid confusing
unrelated help/marketing copy with a post-submit receipt: compare
pre-click and post-click state.

A successful result must carry observed evidence, e.g.:
```json
{
  "status": "Submitted successfully",
  "confirmation_text": "Your application was received",
  "observed_url": "https://verified-example.invalid/confirmation"
}
```
The above is a **schema illustration, not actual Comeet evidence**.

If click may have occurred but the browser timed out, crashed, navigated
unexpectedly, or showed a human-verification challenge without an
authoritative result, record `Submission unconfirmed` and **never
click again automatically**. An HTTP 200, uploaded PDF, disabled button
or missing confirmation email is not by itself proof of acceptance.

For AJSS:
- Only the Application Service persists state and receipts.
- `ReadySubmission` writes a pending receipt **before dispatch**
  in the local adapter path.
- ChatGPT native task path calls `beginChatGPTSubmission`
  immediately before final click, then `completeChatGPTSubmission`
  for observed outcomes.
- Confirmed acceptance may move `Prepared -> Applied`.
- Failed or incomplete attempts go to the project's defined
  manual/dead-letter flows, and uncertain attempts require reconciliation
  before any new submission.

## 9. Practical troubleshooting

| Symptom | What to check next |
| --- | --- |
| GET HTML has no form | The form may appear after JS, iframe loading, or `#showApplyForm` |
| Comeet Careers API returns data but no form | Listings API ≠ application UI; open the actual posting in a real browser |
| Could not find `#inputFirstName` | Examine child frames, frame URLs, tenant-specific markup, blocked script/verification |
| "Submit application" locator matches nothing | Re-inventory exact accessible buttons in the form frame; customized label |
| File chooser never fires | Inspect underlying input, label association, and direct Playwright file upload |
| Both PDF fields exist | Use the **same combined PDF twice**, explicitly |
| New checkbox/dropdown appears | Read the label and policy; stop if consent/answer is not supported |
| CAPTCHA appears | Stop and request authorized human interaction; never bypass |
| Submission clicked but no receipt | `Submission unconfirmed`; preserve evidence; do not retry |
| AJSS MCP is unavailable/405 | Fix MCP transport/endpoint separately; do not equate an API failure with a live website submission |
| Browser/network access blocked | Do not assert live field verification; use supported operator browser host |

## 10. Runbook and completion record

A Comeet form discovery record is complete when it includes:
- exact employer slug + company UID + position UID and verified posting URL;
- which **public read** path worked (inline state / careers API / rendered text);
- observed open-form action, iframe origin/URL and form identity;
- exhaustive control inventory and **unique** final submit locator;
- actual field values sourced from authorized profile (redacted in logs);
- two attachment-acceptance observations for the same combined PDF
  where both Comeet upload fields exist;
- pre-submit screenshot with hover and blockers/validation errors;
- if and only if submitted: durable started intent, one final click,
  actual receipt text, observed URL, persisted outcome.

Do not describe a simulated test, static inspection, or script generation
as a verified live submission.

## Source and version boundaries

- AJSS implementation: `adapters/job_boards/comeet.py`,
  `adapters/browser/comeet.py`, `adapters/browser/comeet.cjs`,
  `adapters/job_boards/submission.py`, `services/ready_submission.py`.
- AJSS workflow: `deployment/chatgpt-stages.md` and
  `skills/application-service/SKILL.md`.
- General website workflow:
  [dynamic-web-form-discovery](../dynamic-web-form-discovery/SKILL.md).
- Comeet documentation: public careers APIs and customizable
  application form widget/settings may evolve. Always confirm the
  rendered form against the current job posting.
