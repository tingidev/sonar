#!/usr/bin/env bash
# Downloads and restores ChEMBL 36 into a local Postgres instance.
# Intended as a Docker entrypoint script — runs once, then Postgres
# serves the loaded data from the persistent volume.
set -euo pipefail

CHEMBL_VERSION="36"
CHEMBL_DUMP_URL="https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/chembl_${CHEMBL_VERSION}_postgresql.tar.gz"
CHEMBL_DB="chembl"
MARKER="/var/lib/postgresql/data/.chembl_loaded"

if [ -f "$MARKER" ]; then
    echo "ChEMBL ${CHEMBL_VERSION} already loaded, skipping download."
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading ChEMBL ${CHEMBL_VERSION} (~1.9 GB)..."
curl -fSL --progress-bar -o "${TMPDIR}/chembl.tar.gz" "$CHEMBL_DUMP_URL"

echo "Extracting archive..."
tar -xzf "${TMPDIR}/chembl.tar.gz" -C "${TMPDIR}"
DUMP_FILE=$(find "${TMPDIR}" -name "*.dmp" | head -1)

if [ -z "$DUMP_FILE" ]; then
    echo "ERROR: Could not find .dmp file in archive. Contents:"
    find "${TMPDIR}" -type f | head -20
    exit 1
fi

echo "Found dump file: ${DUMP_FILE}"

echo "Creating database ${CHEMBL_DB}..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE ${CHEMBL_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${CHEMBL_DB}')
    \gexec
SQL

echo "Restoring ChEMBL ${CHEMBL_VERSION} (this may take 10-30 minutes)..."
# pg_restore returns non-zero on non-fatal warnings (e.g. oversized index
# entries). Allow it to complete, then verify tables actually loaded.
pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$CHEMBL_DB" \
    --no-owner \
    --no-privileges \
    --jobs 2 \
    "$DUMP_FILE" || true

TABLE_COUNT=$(psql -t -A --username "$POSTGRES_USER" --dbname "$CHEMBL_DB" \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")

if [ "$TABLE_COUNT" -lt 10 ]; then
    echo "ERROR: Only ${TABLE_COUNT} tables found after restore — expected 70+."
    exit 1
fi

touch "$MARKER"
echo "ChEMBL ${CHEMBL_VERSION} loaded successfully (${TABLE_COUNT} tables)."
