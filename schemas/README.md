# Versioned schemas

The outcome schema files in this directory are the shared envelope and per-stage outcome enums. All values must be covered in `config/gdtq-routing.json`. The StateTransitionEngine verifies exhaustiveness at startup.

`applicant-profile.schema.json` records the candidate profile format. `personal.calendly_url` is the scheduling link saved during onboarding. Prompt-level schemas live alongside the retained `agents/application/` and `agents/interview-prep/` instructions.
