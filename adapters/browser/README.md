# Browser integration — controlled compatibility surface

**The old browser trial scripts and playbooks are not part of GDTQ's default runtime.** The new `gdtq.stages.submit_prepared` accepts *injected*, approved per-site submission strategies and records a begun/uncertain receipt before attempting external submission.

The existing `comeet.cjs`, `comeet.py`, `chrome_session.cjs` and `submission.py` remain as compatibility adapters. The unmaintained Jobify/RVC/EchoJobs interactive site-trial scripts, recipes, checkpoints and their dedicated tests have been removed. Do not assume their behavior is production-safe; never use them as an implicit fallback or as permission to submit.

Automatic job application submission requires authorization, a verified form/recipient, a saved package and a confirmed receipt. If the submit result is ambiguous (timeout after button press, site error, missing confirmation), do not click submit again without reconciliation. Unknown or unsupported sites move to **Manual Apply Required**.

Strong-model fallback may propose a reusable site adapter, but `gdtq.recipes.RecipeProposalRegistry` stores **data-only** proposals for review and testing; generated code is never auto-imported.

See [GDTQ runbook](../../gdtq/README.md) and [shared job-board framework](../job-boards/README.md).
