# Versioned schemas

`gdtq/` contains the shared envelope and per-stage outcome enums; all values must be covered in `config/gdtq-routing.json`. The StateTransitionEngine verifies exhaustiveness at startup.

`applicant-profile.schema.json` records the candidate profile format. Prompt-level schemas live alongside the retained `agents/application/` and `agents/interview-prep/` instructions.
