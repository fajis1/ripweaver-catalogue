#!/bin/sh
set -eu

umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="/backups/ripweaver-catalogue-${timestamp}.dump"

if [ -e "$destination" ]; then
  echo "Backup destination already exists" >&2
  exit 1
fi

pg_dump --format=custom --no-owner --no-privileges --file="$destination"
echo "Backup created: $(basename "$destination")"
