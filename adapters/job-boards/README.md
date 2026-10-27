# Job-board framework

The current GDTQ booking stage uses the adapter framework's public ATS API-first reader and bounded scraping fallback. Board-specific implementations are in `adapters/job_boards/` (Greenhouse, Ashby, Comeet, Lever and others).

`adapters.job_boards.posting_details.fetch_posting_details(url)` returns normalized posting data through the common framework and validates a full description. The job URL and resolved content source remain distinct.

`submit_prepared` uses explicitly injected, authorized site actions; it checkpoints the attempt before external I/O. Never retry a receipt whose status is unknown. Strong-model site recipes are proposed for review and testing, **never automatically installed or executed**.

See [GDTQ](../../gdtq/README.md).
