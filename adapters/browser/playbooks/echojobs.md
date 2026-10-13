# EchoJobs: blocked trial, 2026-10-13

Scope: `/job/bridgewise-senior-backend-engineer-iasmo`.

Both ordinary headless and headed Chrome trials reached Vercel Security
Checkpoint, not the job/application UI. Headless reported Code 21; headed
reported `Failed to verify your browser`, Code 11. No account or application
was created. No form selectors beyond this gate have been verified.

Reuse `config/browser-sessions/echojobs` for human-assisted verification.
Do not brute-force refresh, solve CAPTCHA, use stealth or alternate/private
endpoints to evade the gate. Once normal access is restored, inspect the
posting, employer, upload controls, consent and final confirmation before
adding new verified actions to site_recipes.json.

Checkpoint: needs_human_verification. Do not count sign-in or account creation
as a submission. Never mark Applied without employer confirmation/reference.
