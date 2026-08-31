# Catalogue security boundaries

## Repository separation

This repository contains the server only. It must not be added to the RipWeaver
desktop repository as a submodule, package dependency, build input, or installer
asset. The desktop may share only a secret-free protocol specification, public
signature-verification keys, and synthetic test vectors.

Server database credentials, administrator credentials, deployment files, and
future signing private keys must remain server-side. Catalogue responses contain
metadata only and must never become an update or executable-delivery channel.

## Public submission quarantine

Every request to `POST /v1/submissions` is untrusted. The boundary:

1. requires `application/json` without content encoding;
2. enforces a 256 KiB limit while streaming, independent of `Content-Length`;
3. rejects duplicate object keys, non-finite numbers, excessive nesting and
   excessive JSON node counts;
4. validates the exact Pydantic schema with unknown fields forbidden;
5. rejects paths, unsafe identifiers, control characters, duplicate structural
   identifiers, invalid assignments and containment cycles; and
6. canonicalizes and hashes the validated model before persistence.

Validated requests are inserted only into `submission_quarantine`. Its database
constraint permits `pending` and `rejected` status values only. It references an
installation identity but has no relationship to catalogue revisions, consensus
assertions, consensus results, lookup events, or credits.

There is a private rejection route and deliberately no approve, promote, vote,
credit, or publish route for quarantine IDs. Legacy approval routes query the
historical `submissions` table and therefore cannot resolve a quarantine ID.

## Published catalogue

Public lookups may continue to read already-reviewed revisions and historical
consensus records. The public submission route cannot create or modify either
source. Tests for historical consensus seed isolated databases through an
explicitly guarded trusted-internal function; production API composition does
not import that function.

The compatibility content hash is an identifier, not security authentication or
proof that a physical disc was observed. A future promotion design must add
independent trust, corroboration, rollback, and response signing before any
quarantined claim can influence clients. That future work must use a new,
reviewed boundary rather than adding an approval method to the quarantine.

## Next cryptographic milestone

The next protocol milestone is per-installation proof of key possession for
submissions and server-signed catalogue responses. Until that is complete,
quarantined data remains non-publishable and protocol schema version 4 advertises
`submissions_quarantined: true` and
`quarantine_publication_enabled: false`.
