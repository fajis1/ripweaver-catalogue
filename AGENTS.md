# RipWeaver Catalogue Repository Guide

This service is a public metadata boundary. Preserve these rules:

- PostgreSQL must remain private; never publish or tunnel port 5432.
- Public responses must not contain absolute paths, media locations, drive
  identities, credentials, private network details, or command environments.
- Never accept media, screenshots, transcripts, logs, or arbitrary attachments.
- Public lookup is read-only and returns only legacy reviewed revisions or
  title-level results confirmed by automatic consensus.
- Lookup charging must count only successful confirmed responses. Retries are
  idempotent, misses are free, and manual continuation remains available after
  the visible support prompt.
- Credit changes are append-only. Payment credits require a verified, exact-
  amount, idempotent webhook and must never be granted from a browser redirect.
- Support payments are disabled by default. Never enable them, contact Stripe,
  or use live payment credentials during tests.
- Every public schema-v1 or schema-v2 submission is untrusted input. It must
  pass the bounded strict JSON validator and enter only the separate
  `submission_quarantine` table. Quarantine status is limited by a database
  constraint to `pending` or `rejected`; quarantine rows have no approval,
  consensus, revision, credit, or public-lookup path.
- Historical automatic-consensus tables and reviewed revisions remain readable
  for backward compatibility. New public submissions must never create or
  update consensus assertions, consensus discs/items, catalogue revisions, or
  contribution credits. Tests for the historical engine must seed it directly
  through the explicitly guarded trusted-internal boundary, never through the
  public API.
- Server-assisted matches may be returned as provisional help but never count
  toward quorum or contribution credit. Evidence provenance may select a
  display label inside one semantic group, but may never break a semantic vote
  tie.
- Whole-disc consistency checks must preserve confirmed independent items and
  demote only affected conflicts. Never make one bonus-title disagreement hide
  unrelated confirmed episodes.
- Contribution credits are append-only, limited to one per installation and
  disc fingerprint, and require the configured fraction of that installation's
  independent assertions to participate in confirmed winners. They are never
  clawed back if later evidence changes consensus.
- Schema-v1 legacy moderation remains only for rows already in the historical
  `submissions` table. An ID returned by the public quarantine endpoint must be
  unknown to every legacy approval route.
- Keep credentials in ignored environment state; never open, print, or commit
  `.env`.
- Database migrations must be explicit and reviewable. Do not auto-create or
  destructively migrate the production schema at application startup.
- Tests use synthetic metadata and isolated databases.
- Inspect `git status` before and after changes and preserve unrelated work.
