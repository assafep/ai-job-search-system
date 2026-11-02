# Local run for Codex and Grok

Follow this file when the user asks to run AJSS locally, start the worker, or onboard the résumé, interviews journal, or processed-applications database. The checklist command is idempotent. List values that are already stored before collecting anything. On each later step, ask before replacing a stored value. Save one answer, then run the checklist again.

A single stage uses the same onboard process: `make onboard ONBOARD_ARGS='--stage STEP'`. That request keeps the Drive client. It does not ask the opening question and it does not advance to another stage. `can_start` stays false. Follow `next` for that stage only.

## Each turn

1. From the repository root, run `make onboard`. The command keeps one onboard process for the whole wizard. That process keeps the Google token, the Drive connection, and files it has already read or written, so later Drive reads and writes do not start from scratch. Do not start a new container for each answer. `make onboard-stop` stops that process.
2. Read the JSON and follow `gate`:
   - `ask`: show `next.instructions`. It names the values already stored, and, when every required value is stored, the result of the checks. Ask that question. Do not show a retrieval step. Do not start the worker. Save yes with `--set wizard=yes` and no with `--set wizard=no`.
   - `stop`: show `opening`. When `can_start` is true, ask whether to start the worker. Start it only after the user says yes: `python -m ajss.cli run`. When `can_start` is false, stop.
   - `run` and `next` is null: show `opening`. When `can_start` is true, ask whether to start the worker. Start it only after the user says yes.
   - `run` and `next.mode` is `confirm`: show `next.title` and `next.instructions`. Ask whether to use the stored value or replace it. Keep it with `--set use=NEXT_ID`. Replace it with `--set replace=NEXT_ID`. Do not start the retrieval instructions on this turn.
   - `run` and `next.mode` is `retrieve`: show `next.title`, `next.instructions`, and each entry in `next.links` as a clickable Markdown link. Ask one question.
3. Save a non-secret answer and run `make onboard` again:

```bash
make onboard ONBOARD_ARGS='--set interview_journal_url=https://docs.google.com/document/d/DOCUMENT_ID/edit'
make onboard ONBOARD_ARGS='--set artifact_root_url=https://drive.google.com/drive/folders/FOLDER_ID'
make onboard ONBOARD_ARGS='--set resume_path=Applicant/resume.pdf'
make onboard ONBOARD_ARGS='--set journal_confirmed=yes'
make onboard ONBOARD_ARGS='--set resume_in_drive=yes'
make onboard ONBOARD_ARGS='--set database_mode=local'
make onboard ONBOARD_ARGS='--set database_choice=existing'
make onboard ONBOARD_ARGS='--set database_choice=vercel'
make onboard ONBOARD_ARGS='--set llm_provider=grok'
make onboard ONBOARD_ARGS='--set llm_provider=local'
make onboard ONBOARD_ARGS='--set llm_base_url=http://host.docker.internal:11434/v1'
make onboard ONBOARD_ARGS='--set llm_model=MODEL'
make onboard ONBOARD_ARGS='--set llm_key=none'
make onboard ONBOARD_ARGS='--set contact_email=ADDRESS'
make onboard ONBOARD_ARGS='--set contact_phone=NUMBER'
make onboard ONBOARD_ARGS='--set calendly_url=https://calendly.com/name/meeting'
make onboard ONBOARD_ARGS='--set calendly_url=none'
make onboard ONBOARD_ARGS='--set wizard=yes'
make onboard ONBOARD_ARGS='--set wizard=no'
make onboard ONBOARD_ARGS='--set use=drive_root'
make onboard ONBOARD_ARGS='--set replace=drive_root'
```

4. Secrets stay in the gitignored `.env` file. Ask the user to edit that file. Do not ask for the value in chat, do not pass it in `ONBOARD_ARGS`, and do not print `.env`. The names are `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_GMAIL_REFRESH_TOKEN`, `GOOGLE_CALENDAR_REFRESH_TOKEN`, `DATABASE_URL`, `AJSS_DATABASE_URL`, and `AJSS_LLM_API_KEY`. `OPENAI_API_KEY` and `XAI_API_KEY` still count when `AJSS_LLM_PROVIDER` is unset. Missing contact details are not entered in `.env`. When the contact step is `retrieve`, ask for that one value, then save the answer with `--set contact_email=` or `--set contact_phone=`. When the Calendly step is `retrieve`, ask for the link and save it with `--set calendly_url=`. Those answers are written onto `Applicant/applicant_profile.json`. Do not ask the user to edit `config/runtime.json` for the link.
5. On the Google step, tell the user to select an existing Cloud project and to create a project only when the account has none. The OAuth client is created inside that project. Tell the user to name it `ajss-local`. Its application type must be Desktop app, not Web application, Android, iOS, or Chrome extension. Tell the user to leave "Use this client for an AI-powered agent" unchecked. This client is for the local worker's Drive and Docs access. Later Gmail and Calendar steps use the same client. It is not an AI agent and it does not use Google MCP. When `next.links` contains an `https://accounts.google.com/` link, give the user that link. After the client id and secret are in `.env`, `make onboard-listen` prints one consent URL, receives the browser redirect, and stores the refresh token without printing it. The redirect URI is `http://127.0.0.1:8765/`. A Desktop app often hides that field.
6. On the Gmail and Calendar steps, guide the user before the consent link. Use the Google account that owns the interviews journal and the same Desktop client named `ajss-local`. Do not create a second client. Leave "Use this client for an AI-powered agent" unchecked. Enable the Gmail API or the Google Calendar API from `next.links`. Open the OAuth consent screen, choose Data access, and add the scope. Gmail says it can view email messages and settings. Calendar says it can view events on calendars. If the app is in testing, the Google account must be a test user. The access is read-only: Gmail does not send mail, and Calendar does not create or change events. Then give the user the consent link and run one listener:

```bash
make onboard-listen ONBOARD_ARGS='--stage gmail'
make onboard-listen ONBOARD_ARGS='--stage calendar'
```

Run one listener at a time. Each stores its own refresh token. Neither replaces `GOOGLE_REFRESH_TOKEN`. Do not print the token, a message, or a calendar event. Keep the grant with `--set use=gmail` or `--set use=calendar`. Grant it again with `--set replace=gmail` or `--set replace=calendar`.

## What the user is confirming

- Google Drive and Docs access for the account that owns the journal.
- Read-only Gmail access, so interview mail can be noticed. This does not send mail.
- Read-only Calendar access, so interview events can be noticed. This does not create or change events.
- The Drive folder. The user copies the browser address bar while that folder is open. The address contains `/drive/folders/`. They do not use Share.
- The interviews journal. The user copies the browser address bar while the document is open. The address contains `/document/d/`. They do not use Share. The first `run` adds queue tabs to that document. `journal_confirmed` must be `yes` for the URL just saved. Saving a different URL clears the confirmation.
- The processed-applications database. When no URL is set, ask whether the user already has a Postgres database or wants a new one created in Vercel. Show the checklist links. Save an existing database with `--set database_choice=existing` and the Vercel guide with `--set database_choice=vercel`. An existing database is `AJSS_DATABASE_URL` in `.env`. A Neon database created in Vercel is stored as `DATABASE_URL`; the worker reads that name first. Do not ask for the URL in chat, do not pass it in `ONBOARD_ARGS`, and do not print the pull file. After the URL is saved, the checklist looks for the queue tables and creates any that are missing. Say that sentence. It does not copy old application history. The setup check requires `gdtq_identities`, `gdtq_items`, `gdtq_checkpoints`, `gdtq_event_log`, and `posting_keys`, and names any missing table or column. `gdtq_checkpoints` is written before the next Bookmarked item is taken. An absent `posting_keys` table is created empty. An existing one is left as it is. `database_mode=local` creates a private SQLite file and does not contain that history. Say so before setting it. Replacing the database asks the same choice again and does not connect until the URL in `.env` changes, or until the user chooses the URL already stored there.
- The model provider. Ask which one, then save `llm_provider` as `grok`, `openai`, `anthropic`, `gemini`, or `local`. The checklist writes `AJSS_LLM_PROVIDER`. For `local`, the next answer is the OpenAI-compatible base URL (`llm_base_url`) and then the model id (`llm_model`). A server on this machine is `http://host.docker.internal:<port>/v1` because the worker runs in Docker. Show only the key link on the key step. Tell the user to name the key `ajss-local`. The user puts `AJSS_LLM_API_KEY` in `.env`. For a local server with no key, save `llm_key=none`. A Codex or Grok login does not replace the key.
- Do not ask the user to write `applicant_profile.json`. After the key and the résumé are ready, `make onboard` creates `Applicant/applicant_profile.json` from the résumé PDF and skips the write when that PDF is unchanged. Email and phone are required. A value already taken from the résumé is kept. When a contact step is `confirm`, ask whether to use the saved value or provide a different one, and do not read the saved email or phone aloud. When it is `retrieve`, ask for that one detail and save the reply with `--set`. When the Calendly step is `confirm`, the stored link can be read aloud. When it is `retrieve`, ask for the link and save the reply with `--set calendly_url=` using an `https://calendly.com/…` link. If the user has no link, save `--set calendly_url=none`. The answer is written onto the profile and added to each cover letter.

## Boundaries

- Do not start a second worker. One process writes the journal.
- Do not enable live submission in this flow. `AJSS_SUBMISSION_BACKEND` stays unset.
- Do not invent a résumé, a profile, a journal URL, or a successful setup.
- If the command fails, show the error and stay on the same step.
- `python -m ajss.cli bootstrap` only prepares mocked integration tests. It is not this onboarding flow.
