# Run AJSS locally

Codex and Grok follow [agents/local-run/AGENTS.md](../agents/local-run/AGENTS.md) when asked to run the system on this machine. The setup command can be repeated. Completed steps stay completed.

```bash
make onboard
```

The command prints JSON. `next` is the one piece still missing. `ready` means the worker can start. Each step includes links that open the page where that piece is created.

`make onboard` keeps one onboard process running for the whole wizard. Later steps reuse that process, its Google token, its Drive connection, and the files it has already read or written. A later read of those files does not download them again. `make onboard-stop` stops it. `make onboard-listen` is separate: it prints the Google consent link and waits for the browser.

The first report lists any values that are already stored. When every required value is stored, that same report runs the setup checks (Google, the Drive folder, the interviews journal, the database queue tables, the résumé PDF, the model API, the applicant profile, and the Calendly link) and asks whether to run the wizard. Read-only Gmail and Calendar access are required before that question. When some values are stored and some are missing, it names both and asks whether to proceed. During the wizard, a step whose value is already stored asks whether to use that value or replace it. The checks and the list do not print secrets or contact details. `python -m ajss.cli sanity` runs the same checks on their own.

One stage runs on that same process, with the same Drive connection:

```bash
make onboard ONBOARD_ARGS='--stage gmail'
```

The report follows that stage only. It does not ask the opening question, and it does not move on to the next stage.

| Step | What to provide | Where it is saved |
| --- | --- | --- |
| Google account | OAuth client id, client secret, and a refresh token | `.env` |
| Drive folder | Address bar after the folder is open. Do not use Share. | `config/runtime.json` |
| Interviews journal | Address bar while the document is open. Do not use Share. | `config/runtime.json` |
| Confirm the journal | `yes` for that exact document | `var/onboarding-state.json` |
| Processed applications | An existing Postgres URL as `AJSS_DATABASE_URL`, or a Neon database created in Vercel (`DATABASE_URL`) | `.env` |
| Résumé | Drive path such as `Applicant/resume.pdf` | `.env` as `GOOGLE_RESUME_DRIVE_PATH` |
| Model provider | `grok`, `openai`, `anthropic`, `gemini`, or `local` | `.env` as `AJSS_LLM_PROVIDER` |
| Custom or local server | OpenAI-compatible base URL | `.env` as `AJSS_LLM_PROVIDER` |
| Model API key | `AJSS_LLM_API_KEY` | `.env` |
| Applicant profile | Created by the system from the résumé PDF | `Applicant/applicant_profile.json` in the Drive folder |
| Missing email or phone | The reply, only when the résumé omitted it | `Applicant/applicant_profile.json` |
| Calendly link | The reply, an `https://calendly.com/…` link | `Applicant/applicant_profile.json` and each cover letter |
| Gmail read access | Consent for the read-only Gmail scope | `.env` as `GOOGLE_GMAIL_REFRESH_TOKEN` |
| Calendar read access | Consent for the read-only Calendar scope | `.env` as `GOOGLE_CALENDAR_REFRESH_TOKEN` |

Useful pages:

- [Select a Google Cloud project](https://console.cloud.google.com/projectselector2/home/dashboard)
- [Create a project if you have none](https://console.cloud.google.com/projectcreate)
- [Enable the Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
- [Enable the Docs API](https://console.cloud.google.com/apis/library/docs.googleapis.com)
- [Enable the Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
- [Enable the Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
- [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
- [Create an OAuth client](https://console.cloud.google.com/apis/credentials)
- [Google Drive](https://drive.google.com/drive/my-drive)
- [Google Docs](https://docs.google.com/)
- [Create a Neon database in Vercel](https://vercel.com/marketplace/neon/neon)
- [Vercel dashboard](https://vercel.com/dashboard)
- [Neon](https://console.neon.tech)
- [Grok API keys](https://console.x.ai/team/default/api-keys)
- [OpenAI API keys](https://platform.openai.com/api-keys)
- [Anthropic API keys](https://platform.claude.com/settings/keys)
- [Gemini API keys](https://aistudio.google.com/apikey)

Select the existing Google Cloud project for this system. Create a project only when the account has none, then select that project. Inside it, enable Drive and Docs, add your Google account as a test user on the consent screen, and create an OAuth client. Name the client `ajss-local`. The application type must be Desktop app. Do not choose Web application, Android, iOS, or Chrome extension. Leave "Use this client for an AI-powered agent" unchecked. This client is for the local worker's Drive and Docs access. The Gmail and Calendar steps use the same client. It is not an AI agent and it does not use Google MCP. If the form asks for a redirect URI, use:

```text
http://127.0.0.1:8765/
```

Put the client id and client secret in `.env`. Do not commit that file. Then:

```bash
make onboard-listen
```

Open the printed `https://accounts.google.com/...` link and approve Drive access. The command stores the refresh token and does not print it.

Gmail and Calendar are separate read-only grants. Enable the matching API, then open the consent screen, choose Data access, and add the scope. Gmail is the scope that views email messages and settings. Calendar is the scope that views events on calendars. If the app is in testing, the Google account must be a test user. Use the same `ajss-local` Desktop client. Leave "Use this client for an AI-powered agent" unchecked. Do not create a second client. Then run one listener at a time:

```bash
make onboard-listen ONBOARD_ARGS='--stage gmail'
make onboard-listen ONBOARD_ARGS='--stage calendar'
```

Each command prints its own consent link, waits for the browser, and stores a separate refresh token. Neither command replaces the Drive token. Gmail access does not send mail. Calendar access does not create or change events.

When no database URL is set, the checklist asks whether to use a Postgres database that is already running or to create one with Neon in Vercel. An existing URL goes in `.env` as `AJSS_DATABASE_URL`. The Vercel integration stores `DATABASE_URL`, and the worker reads that name first. Do not paste either URL into the chat. After the URL is saved, the checklist looks for its queue tables and creates any that are missing. It does not copy older application rows. The setup check requires `gdtq_identities`, `gdtq_items`, `gdtq_checkpoints`, `gdtq_event_log`, and `posting_keys`, and it names any missing table or column. `gdtq_checkpoints` is the table the worker writes before taking the next Bookmarked item. An absent legacy `posting_keys` table is created empty. An existing one is left as it is. A local SQLite database is available with `make onboard ONBOARD_ARGS='--set database_mode=local'`. It does not contain the processed-applications history.

Say the model provider (`grok`, `openai`, `anthropic`, `gemini`, or `local`). The checklist writes `AJSS_LLM_PROVIDER` and then shows the matching key page. Name that key `ajss-local`, the same name offered for the OAuth client. Put the key in `.env` as `AJSS_LLM_API_KEY`. For `local`, give the OpenAI-compatible base URL; the worker stores that URL as `AJSS_LLM_PROVIDER`. A server on this machine is reached from Docker at `http://host.docker.internal:<port>/v1`. Confirm `--set llm_key=none` when that server does not use a key. `OPENAI_API_KEY` and `XAI_API_KEY` still select OpenAI or Grok when `AJSS_LLM_PROVIDER` is unset.

Email and phone are required on the applicant profile. When the résumé already contains one, it is kept. When it does not, the checklist asks for that value and writes the answer onto `Applicant/applicant_profile.json`. That update does not download the résumé again. The checklist also asks for a Calendly link and writes that answer onto the same profile. Each cover letter includes it. Saying there is no link skips it.

The first `python -m ajss.cli run` adds queue tabs to the confirmed journal. Start it only after `make onboard` reports `"ready": true` and you want that document to be edited.

Non-secret answers are saved by repeating `make onboard` with one `--set` assignment, as listed in the agent instructions. Secret values are typed into `.env` only.
