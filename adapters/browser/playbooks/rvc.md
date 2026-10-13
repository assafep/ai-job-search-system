# RVC Global: verified trial, 2026-10-13

Scope: `/vacancy/view/bridgewise-senior-backend-engineer-544071`.
These are observed UI steps, not a verified end-to-end submission adapter.

1. Reuse `config/browser-sessions/rvc`. Decline optional analytics.
2. `Apply Now` opens CV/profile onboarding. With explicit account-creation
   authorization, use `button:has-text("SELECT FILE")` and the unchanged PDF.
   Review the required terms checkbox in the modal, then continue once.
3. A headless trial showed Cloudflare Quick security check. A normal headed
   Chrome trial passed automatically; no solving, stealth, or bypass was used.
   If verification remains, request human assistance instead of retry loops.
4. Wait for CV analysis and inspect Review Profile. Verify `#fullName`,
   `#email`, and `#yearsOfExperience` against applicant_profile. The parser
   extracted 16 years; the supplied profile supports 24+ years (resume starts
   2002), so it was corrected to 24. Do not trust extracted values blindly.
5. `Confirm & Next →` validates native language. This trial stopped with
   `Native language is required`. English and Spanish in the resume do not
   establish a native language; ask the applicant rather than infer from country.
6. Preferences and Alerts are later, unverified stages. Unknown mandatory
   preferences require applicant input. Do not opt into optional marketing.

The onboarding UI states RVC does not submit uploaded CVs to employers.
Profile creation is NOT employer submission. Inspect the eventual application
route and require observed employer confirmation before marking Applied.
Current checkpoint: profile_review / needs_native_language, no submission.
