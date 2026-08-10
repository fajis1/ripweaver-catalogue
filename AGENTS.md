# RipWeaver Catalogue Repository Guide

This service is a public metadata boundary. Preserve these rules:

- PostgreSQL must remain private; never publish or tunnel port 5432.
- Public responses must not contain absolute paths, media locations, drive
  identities, credentials, private network details, or command environments.
- Never accept media, screenshots, transcripts, logs, or arbitrary attachments.
- Public lookup is read-only and returns reviewed revisions only.
- Contributions enter a pending queue and require separate moderation.
- Keep credentials in ignored environment state; never open, print, or commit
  `.env`.
- Database migrations must be explicit and reviewable. Do not auto-create or
  destructively migrate the production schema at application startup.
- Tests use synthetic metadata and isolated databases.
- Inspect `git status` before and after changes and preserve unrelated work.
