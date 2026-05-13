#!/usr/bin/env bash
set -euo pipefail

TPCDS_DB="tpcds"

echo "Creating database ${TPCDS_DB}..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE ${TPCDS_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${TPCDS_DB}')
    \gexec
SQL

echo "Creating TPC-DS schema (24 tables)..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$TPCDS_DB" -f /schema/tpcds.sql

echo "Loading generated data..."
for csv in /data/*.csv; do
    table=$(basename "$csv" .csv)
    echo "  Loading ${table}..."
    psql --username "$POSTGRES_USER" --dbname "$TPCDS_DB" -c "\COPY ${table} FROM '${csv}' WITH (FORMAT csv, HEADER true, DELIMITER '|', NULL '')"
done

TABLE_COUNT=$(psql -t -A --username "$POSTGRES_USER" --dbname "$TPCDS_DB" \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")

if [ "$TABLE_COUNT" -lt 24 ]; then
    echo "ERROR: Only ${TABLE_COUNT} tables found after load — expected 24."
    exit 1
fi

echo "TPC-DS loaded successfully (${TABLE_COUNT} tables)."
