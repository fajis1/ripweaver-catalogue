# RipWeaver Catalogue Repository Guide

This service is a public metadata boundary. Preserve these rules:

- PostgreSQL must remain private; never publish or tunnel port 5432.
- Public responses must not contain absolute paths, media locations, drive
  identities, credentials, private network details, or command environments.
- Never accept media, screenshots, transcripts, logs, or arbitrary attachments.
- Public lookup is read-only and returns reviewed revisions only.
- Lookup charging must count only successful reviewed responses. Retries are
  idempotent, misses are free, and manual continuation remains available after
  the visible support prompt.
- Credit changes are append-only. Payment credits require a verified, exact-
  amount, idempotent webhook and must never be granted from a browser redirect.
- Support payments are disabled by default. Never enable them, contact Stripe,
  or use live payment credentials during tests.
- Contributions enter a pending queue and require separate moderation.
- Keep credentials in ignored environment state; never open, print, or commit
  `.env`.
- Database migrations must be explicit and reviewable. Do not auto-create or
  destructively migrate the production schema at application startup.
- Tests use synthetic metadata and isolated databases.
- Inspect `git status` before and after changes and preserve unrelated work.
