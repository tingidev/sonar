#!/usr/bin/env bash
# Downloads and restores AdventureWorks into a local Postgres instance.
# Intended as a Docker entrypoint script — runs once, then Postgres
# serves the loaded data from the persistent volume.
set -euo pipefail

AW_DUMP_URL="https://github.com/timchapman/postgresql-adventureworks/raw/refs/heads/main/AdventureWorksPG.gz"
AW_DB="adventureworks"
MARKER="/var/lib/postgresql/data/.adventureworks_loaded"

if [ -f "$MARKER" ]; then
    echo "AdventureWorks already loaded, skipping download."
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading AdventureWorks (~18.6 MB)..."
wget -q -O "${TMPDIR}/AdventureWorksPG.gz" "$AW_DUMP_URL"

echo "Creating database ${AW_DB}..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE ${AW_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${AW_DB}')
    \gexec
SQL

echo "Creating required extensions..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$AW_DB" <<-SQL
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    CREATE EXTENSION IF NOT EXISTS tablefunc;
SQL

echo "Restoring AdventureWorks (this may take a few minutes)..."
# pg_restore returns non-zero on non-fatal warnings (e.g. Azure-specific
# extension errors). Allow it to complete, then verify tables actually loaded.
pg_restore \
    -U "$POSTGRES_USER" \
    -d "$AW_DB" \
    --no-owner \
    --no-acl \
    "${TMPDIR}/AdventureWorksPG.gz" || true

TABLE_COUNT=$(psql -t -A --username "$POSTGRES_USER" --dbname "$AW_DB" \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema');")

if [ "$TABLE_COUNT" -lt 68 ]; then
    echo "ERROR: Only ${TABLE_COUNT} tables found after restore — expected 68+."
    exit 1
fi

touch "$MARKER"
echo "AdventureWorks loaded successfully (${TABLE_COUNT} tables)."
