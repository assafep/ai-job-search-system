---
name: dynamic-web-form-discovery
description: Discover, inspect, and safely operate JavaScript-rendered websites and multi-step application forms. Use when initial HTTP HTML is incomplete, controls appear after clicking, fields live in iframes or shadow DOM, network responses supply data, or an Apply/Submit action needs trustworthy field and receipt evidence.
---

# Dynamic website and application-form discovery

A **workflow skill** for an interactive browser-capable runtime. A search result,
\`requests.get()\`, parsed HTML, or an LLM guess does **not** establish the
contents of a client-rendered form. Discover from browser-rendered evidence, keep
a field inventory, and distinguish a form being filled from an application being
received.

Use [the Comeet-specific skill](../comeet-application/SKILL.md) whenever a
posting is hosted by Comeet. The specialist guide overrides site-specific
heuristics here; the safety, evidence and confirmation rules remain shared.

## 1. Establish what is actually available

1. Identify the authoritative URL and expected company, position, and
   application identity. Record the canonical/redirected URL.
2. Determine which **real** browser the task is allowed to use:
   - **Interactive ChatGPT native Browser/Computer Use** when the AJSS
     \`validate-bookmarked\` or \`submit-prepared\` task contract requires it.
     Use the supplied stage task protocol; a Python script cannot secretly
     stand in for that browser.
   - **Playwright on a workstation** only for an explicitly supported and
     configured local-browser workflow. Python/Node and installed Chromium
     must exist on that workstation.
   - **Chrome DevTools** for human debugging, using Network, Elements, Console
     and Application tabs on a browser session under the operator's control.
3. If only a fetch/search tool is present, report **static inspection only**.
   If browser access returns 403, network/DNS failure, blocked-by-administrator,
   CAPTCHA, or login challenge, label the form **not verified** and request
   the appropriate user-controlled access. Do not manufacture screenshots or
   assert a submission happened.
4. Separate **discovery mode** (read-only GET and safe UI navigation),
   **preview mode** (fill and hover, never click final submit), and
   **submission mode** (one authorized final click plus receipt inspection).
   Never switch modes implicitly.

For AJSS, **Prepared** queue membership is the application submission
authorization; a final browser click still requires the durable
\`beginChatGPTSubmission\`/receipt boundary defined by the current stage
contract. Follow the deployed stage ownership. Do not infer that a local
Playwright capability is available merely because repository code mentions it.

## 2. Discover the rendered content, progressively

Use the smallest set of operations that explains the **relevant job and
application flow**, not an unrestricted crawl of an employer website.

| Pass | Inspection | Evidence / next decision |
| --- | --- | --- |
| A: HTTP document | Normal GET, redirects, status, content type, source HTML | Is there job text, JSON-LD, an inline state blob, a form, script references, or only an app shell? |
| B: Browser boot | Real browser with JavaScript enabled; subscribe to events **before** \`goto\` | Final page URL, document title, screenshot, visible text and runtime errors |
| C: Network graph | Request/response/failed requests, XHR/fetch/GraphQL, relevant JSON bodies | Which successful **read** response supplies position/details/form configuration? |
| D: DOM graph | Main frame + every same/cross-origin child frame, visible controls, shadow roots | Form boundaries, labels, hidden/conditional sections, buttons |
| E: Safe transitions | Click an identified *open application* control, select a noncommittal tab, scroll, wait | Re-enumerate frames, controls, network and visible text after each transition |
| F: Form preparation | Fill known data, upload authorized files, select approved choices | Detect dynamically added or newly required fields and validation messages |
| G: Final boundary | Identify uniquely the *final submit* control and receipt contract | Stop at preview, or persist submission intent and click once when authorized |

Maintain a state graph:
\`initial -> job-details -> apply-entry -> form-loaded -> partially-filled ->
ready-to-submit -> receipt/error\`. For each edge save what action caused it,
which frames/requests appeared, and whether the action could create an account,
consent to something, or transmit an application. Do **not** use backtracking
or replays once the final click might have happened.

**Do not treat \`DOMContentLoaded\` or \`networkidle\` as proof of completion.**
Prefer an explicit form/iframe/control wait, plus a bounded observation interval
for asynchronous fields. A completed top-level document can still contain an
unloaded form iframe.

## 3. Intercept and interpret requests/responses correctly

Use \`page.on("request")\`, \`page.on("response")\`,
\`page.on("requestfinished")\`, \`page.on("requestfailed")\`, and
\`page.on("frameattached")\`. In human Chrome, inspect the Network tab with
"Preserve log" enabled before opening the form.

- For each relevant request record: time/order, navigation action, initiator
  frame, method, sanitized URL, resource type, HTTP status or failure reason,
  redirect target, response \`content-type\`, and whether it changed the UI.
- For a **public GET** JSON response, parse its root shape, keys, list lengths,
  position UID and related links. Follow any documented position/details URL
  and verify it matches the visible job. Treat a 200 JSON error object as
  an error, not a valid posting. Distinguish \`requestfailed\` transport failures
  from an HTTP 404/429/503 response.
- For GraphQL, interpret the **observed operation name, variables and response**
  but do not enumerate undocumented mutations. If pagination exists, follow
  the documented next cursor/page with a strict bound.
- For POST/PUT/PATCH, identify **whether it is a submission**, upload,
  validation, account creation, or analytics call. Do not replay an observed
  application POST merely to discover its schema.
- Inspect fetch/XHR responses **after body completion**; event callbacks should
  not block browser operation. Avoid logging response bodies or request payloads
  containing CV content, identity data, cookies, CSRF values or application
  tokens. Record only metadata and redacted shape.
- When nothing useful appears in XHR: inspect HTML for JSON-LD, hydration state,
  initial global variables, data attributes and embedded scripts; the content
  may have arrived inline or through a child frame, rather than in fetch/XHR.

### Where to make HTTP requests

**Preferred for UI and final submission:** the browser instance that rendered
the page, including its real frame context, cookies, redirects, validation,
CSRF-handling and browser origin rules. Use Playwright locators and UI actions.

**For documented public read-only API calls:** use the browser's
\`context.request.get(url, params=...)\` or an ordinary Python
\`requests.get\` if the endpoint is truly public and does not require session
state. The two are not interchangeable for an authenticated form workflow.

**Never** construct a raw submit POST from a guessed endpoint, extract private
CSRF/session credentials for an unrelated HTTP client, or forge \`Origin\`,
\`Referer\`, cookies, proxy identities or CAPTCHA answers. Respect the
website's terms, rate limits, and access controls. Access to a public careers
API is not authority to call internal applicant/mutation endpoints.

### Request parameters and headers

Start with **no invented parameters**. Add only parameters the page actually
sent or public documentation specifies, e.g. a documented \`details=true\`,
\`page\`, \`cursor\` or locale. Preserve IDs **exactly** (including dots/leading
zeros), encode query values with a URL builder, and verify response identity.
Do not try arbitrary \`render=1\`, \`ajax=true\`, \`callback\`, secret token
guesses or parameter brute force to make a page work.

Use normal HTTPS and the browser's natural headers for browser actions.
For a public read-only fetch, reasonable headers are a truthful application
\`User-Agent\`, a content-specific \`Accept\` (\`text/html\` or
\`application/json\`) and an optional real \`Accept-Language\`. Follow
documented request timeouts, back off from 429, and enforce modest concurrency.
Do not disable TLS checks, invent navigation headers, or use rotating
proxies to defeat controls. Never place credentials in saved diagnostic logs.

## 4. Enumerate **all** active form controls

After every material DOM transition, inspect **every frame**, not just
\`page.locator()\` in the main page. Treat \`iframe\` navigation and
shadow-DOM components separately. Prefer Playwright's
\`get_by_role\`, \`get_by_label\`, \`get_by_placeholder\` and \`frame_locator\`;
CSS IDs are useful corroboration, not universal truth.

Enumerate at least:
\`input\` (all types), \`textarea\`, \`select\`,
\`button\`, \`input[type=submit]\`, \`[role=button]\`,
\`[role=combobox]\`, \`[role=checkbox]\`,
\`[role=radio]\`, \`[contenteditable]\`, file-upload triggers,
and meaningful links. Also check labels, legends, help text, error messages,
accessible names and \`aria-describedby\`.

Store a **field inventory** keyed by stable UI identity, including:

\`\`\`json
{
  "frame_url": "https://example.com/form",
  "form_id": "optional-id",
  "tag": "input",
  "type": "email",
  "id": "inputEmail",
  "name": "email",
  "label": "Email",
  "placeholder": "",
  "required": true,
  "visible": true,
  "enabled": true,
  "options": [],
  "accepted_file_types": null,
  "mapped_profile_path": "personal.email",
  "fill_status": "not-filled",
  "selector_evidence": "get_by_label('Email')",
  "source": "observed-rendered-DOM"
}
\`\`\`

For each control, collect \`required\` **and** \`aria-required\`, \`disabled\`,
visibility, \`autocomplete\`, \`multiple\`, \`accept\`, \`pattern\`,
\`min\`/\`max\` and \`minlength\`/\`maxlength\` when meaningful. Derive labels
from explicit \`label[for]\`, wrapping labels, \`aria-label\`,
\`aria-labelledby\`, placeholder, legend/group context, and then nearby text;
save uncertainty instead of silently guessing.

For select/combobox/radio fields, list **available choices from the UI**.
For checkboxes, distinguish a mandatory privacy/terms acknowledgment from
optional marketing or data-sharing consent. Do not tick optional consent to
remove a blocker. If the candidate's choice is unknown, ask or route to manual
work instead of inventing it.

For file fields inspect the actual \`input[type=file]\` even when visually
hidden. Record \`accept\`, \`multiple\`, enabled state, and whether an upload
widget uses a button/label to open a file chooser. After upload, verify the
selected file's **basename and UI confirmation**; a clicked upload trigger
alone is not evidence that the file was accepted.

Persist both the *pre-fill* and *post-fill* inventories. If values or fields
change after typing, run the inventory again until stable.

## 5. Identify the **correct** Apply button

Three different controls may use "Apply":

1. A job-card link to the detailed posting.
2. An **open form** button that reveals an iframe/modal/embedded form.
3. The **final submission** button inside the application form.

Do not confuse them. Confirm the final button belongs to the populated form,
is visibly enabled, and has the correct accessible name (some sites use
"Send", "Submit application" or customized text). Verify one and only one
matching actionable control within the **correct frame**. If several match,
scope to the parent form and inspect the DOM rather than using \`.first()\`
blindly.

For preview, scroll to the final button, call \`locator.hover()\`, and capture
a screenshot. Screenshots commonly omit the system mouse pointer; optionally
overlay a **clearly marked cursor indicator** for human review. **Do not
click** in preview mode. Never synthesize a success message or infer
submission from a preview screenshot.

## 6. Fill by evidence; rerender and validate

Use a verified profile and an exact saved application package; never invent
phone numbers, employers, education, citizenship, work authorization or
candidate answers. Match fields by semantic label + UI context, not by
position in a list. Fill in small batches, then re-enumerate the DOM and
validation messages. Dispatch normal browser input/change events through
\`locator.fill()\`, \`select_option()\`, \`check()\`, and
\`set_input_files()\`; setting \`element.value\` by script alone can bypass
framework handlers.

Stop before final click if any of the following applies: missing **required**
answer, unsupported custom widget, uncertain or misleading consent, captcha,
failed attachment, wrong job identity, visibly invalid data, unresolved
client-side validation, missing durable submission authorization, or an
unverified submit control.

## 7. One-shot submission and outcomes

1. Make an immutable preflight record: exact posting URL and role, profile
   provenance, package filename/hash, required-field inventory, UI evidence
   and stable task/application ID.
2. If AJSS-native submission, call \`beginChatGPTSubmission\` **immediately
   before** clicking and click **only if** it returns \`may_submit=true\`.
   For a configured local worker, use its existing durable pending receipt
   **before** dispatch. Never manufacture an intent marker after the fact.
3. Arm navigation/response observers **before** the final click; click the
   button **once**. From that instant, a timeout, crash, lost response or
   human-verification page is **Submission unconfirmed** until reconciled,
   not a license to click again.
4. Inspect the rendered confirmation, final URL, visible receipt/reference
   and explicit successful-application text. A request status 200, file
   upload success or absent confirmation email is **not** sufficient.
5. Record the observed outcome through the Application Service:
   - \`Submitted successfully\` / \`Already submitted\`: actual receipt
     evidence, observed URL, no conflicting blockers.
   - \`Prepared but not submitted\`: never clicked; fixable form blockers.
   - \`Blocked\`: known pre-click block (CAPTCHA, missing access, etc.).
   - \`Submission unconfirmed\`: clicked or may have clicked, but no receipt.
   Never mark \`Applied\` without verified success.
6. Do not retry an unconfirmed or already-submitted application; investigate
   the existing AJSS receipt and employer confirmation first.

A confirmation email is useful supporting evidence, but **not** mandatory
proof; its absence does not establish failure.

## 8. Required artifacts and operational boundaries

Save a sanitized discovery summary, field inventory, page/frame URLs,
redacted request metadata, relevant form screenshot(s), preflight blockers,
and (only if submitted) the **observed** confirmation. Treat screenshots
and traces after filling as sensitive candidate data; store privately.
Never copy session cookies, auth headers, CSRF tokens or applicant PDFs into
a public log, GitHub issue, or LLM prompt.

The repo's optional non-submitting inspector is
[\`scripts/inspect.py\`](scripts/inspect.py). Run it on an authorized browser
workstation, not in an environment where website access is blocked:

\`\`\`bash
python -m pip install playwright
python -m playwright install chromium
python skills/dynamic-web-form-discovery/scripts/inspect.py \
  "https://example.com/careers/job" --headed --out .local-form-inspection
\`\`\`

Inspect the resulting JSON and screenshot; it never fills personal data and
never performs final submission. Use \`--entry-selector '#showApplyForm'\`
**only after verifying** that selector opens the form rather than submitting.

For actual submission, follow the deployed AJSS task contract and a permitted
interactive browser. Do not treat the inspector as a submitter.

## 9. Completion checklist

A successful *discovery* has an observed and reproducible job identity,
rendered page and frame structure, request/response graph, field inventory
(including hidden, conditional and custom widgets), identified unique **final**
submit control, validation blockers and screenshot. A successful *submission*
additionally has a durable begun intent, one recorded click, explicit receipt,
observed URL and persisted outcome.

If any item is unknown, explicitly say **unknown**, preserve the collected
evidence, and stop at the last safe boundary.

## References

- Playwright Python: [locators](https://playwright.dev/python/docs/locators),
  [network](https://playwright.dev/python/docs/network),
  [request events](https://playwright.dev/python/docs/api/class-request),
  [file chooser](https://playwright.dev/python/docs/api/class-filechooser).
- AJSS: \`deployment/chatgpt-stages.md\`,
  \`services/ready_submission.py\`,
  \`adapters/job_boards/submission.py\`,
  \`skills/application-service/SKILL.md\`.
