# Jobify: verified trial, 2026-10-13

Scope: Bridgewise / Senior Backend Engineer, `/jobs/4475943838-in`.
These are observed UI steps, not a verified end-to-end submission adapter.

1. Reuse the protected `config/browser-sessions/jobify` session. Check the
   signed-in greeting before registering again.
2. If login reports the email does not exist, `/ab-type` is the registration
   route. With explicit account-creation authorization, use the supplied
   unchanged package via `label[for="cv"]`, accept `#agreeTerms`, then click
   `#continueUploadBtn` once. Resume parsing can take about 30 seconds;
   inspect rather than clicking again. The verified trial ended signed in.
3. For this job, use the second
   `button[data-bs-target="#servingFTModal"]` (`index: 1`). The first is a
   sticky clone outside the viewport; repeated clicks on it time out.
   Revalidate which control is in the viewport if the page layout changes.
4. Inspect `#servingFTModal`. This listing explicitly says applications must
   continue on an external job board. The `#main` link points to LinkedIn job
   4475943838. Following it in this trial landed at LinkedIn's sign-in page.
5. The applicant already has a LinkedIn profile: request interactive sign-in,
   not a duplicate LinkedIn account. Handoff is NOT employer submission.

Resume checkpoints: account_created -> external_handoff -> needs_login.
Only explicit employer confirmation/reference can count as submitted.
Do not assume other Jobify listings use this route: Quick Apply is separate.
