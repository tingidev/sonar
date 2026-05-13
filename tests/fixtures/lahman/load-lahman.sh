#!/usr/bin/env bash
# Downloads and loads the Lahman Baseball Database into a local Postgres instance.
# Intended as a Docker entrypoint script — runs once, then Postgres
# serves the loaded data from the persistent volume.
set -euo pipefail

LAHMAN_URL="https://github.com/cbwinslow/baseballdatabank/archive/refs/heads/master.zip"
LAHMAN_DB="lahman"
MARKER="/var/lib/postgresql/data/.lahman_loaded"

if [ -f "$MARKER" ]; then
    echo "Lahman Baseball Database already loaded, skipping download."
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading Lahman Baseball Database (~7 MB)..."
curl -fSL --progress-bar -o "${TMPDIR}/lahman.zip" "$LAHMAN_URL"

echo "Extracting archive..."
unzip -q "${TMPDIR}/lahman.zip" -d "${TMPDIR}"

CORE_DIR="${TMPDIR}/baseballdatabank-master/core"
CONTRIB_DIR="${TMPDIR}/baseballdatabank-master/contrib"

if [ ! -d "$CORE_DIR" ]; then
    echo "ERROR: Could not find core directory. Contents:"
    find "${TMPDIR}" -type d | head -20
    exit 1
fi

echo "Stripping UTF-8 BOM from CSV files..."
find "$CORE_DIR" "$CONTRIB_DIR" -name "*.csv" -exec sed -i 's/^\xef\xbb\xbf//' {} \;

echo "Creating database ${LAHMAN_DB}..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE ${LAHMAN_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${LAHMAN_DB}')
    \gexec
SQL

echo "Creating schema..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$LAHMAN_DB" \
    -f /schema/schema.sql

echo "Loading core CSV files..."

load_csv() {
    local table="$1"
    local file="$2"
    if [ -f "$file" ]; then
        psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$LAHMAN_DB" \
            -c "\COPY ${table} FROM '${file}' WITH (FORMAT csv, HEADER true, DELIMITER ',', NULL '')"
        echo "  Loaded ${table}"
    else
        echo "  WARNING: ${file} not found, skipping ${table}"
    fi
}

load_csv People         "${CORE_DIR}/People.csv"
load_csv Batting        "${CORE_DIR}/Batting.csv"
load_csv BattingPost    "${CORE_DIR}/BattingPost.csv"
load_csv Pitching       "${CORE_DIR}/Pitching.csv"
load_csv PitchingPost   "${CORE_DIR}/PitchingPost.csv"
load_csv Fielding       "${CORE_DIR}/Fielding.csv"
load_csv FieldingOF     "${CORE_DIR}/FieldingOF.csv"
load_csv FieldingOFsplit "${CORE_DIR}/FieldingOFsplit.csv"
load_csv FieldingPost   "${CORE_DIR}/FieldingPost.csv"
load_csv AllstarFull    "${CORE_DIR}/AllstarFull.csv"
load_csv Appearances    "${CORE_DIR}/Appearances.csv"
load_csv Managers       "${CORE_DIR}/Managers.csv"
load_csv ManagersHalf   "${CORE_DIR}/ManagersHalf.csv"
load_csv Teams          "${CORE_DIR}/Teams.csv"
load_csv TeamsFranchises "${CORE_DIR}/TeamsFranchises.csv"
load_csv TeamsHalf      "${CORE_DIR}/TeamsHalf.csv"
load_csv SeriesPost     "${CORE_DIR}/SeriesPost.csv"
load_csv HomeGames      "${CORE_DIR}/HomeGames.csv"
load_csv Parks          "${CORE_DIR}/Parks.csv"

echo "Loading contrib CSV files..."

load_csv AwardsManagers      "${CONTRIB_DIR}/AwardsManagers.csv"
load_csv AwardsPlayers       "${CONTRIB_DIR}/AwardsPlayers.csv"
load_csv AwardsShareManagers "${CONTRIB_DIR}/AwardsShareManagers.csv"
load_csv AwardsSharePlayers  "${CONTRIB_DIR}/AwardsSharePlayers.csv"
load_csv CollegePlaying      "${CONTRIB_DIR}/CollegePlaying.csv"
load_csv HallOfFame          "${CONTRIB_DIR}/HallOfFame.csv"
load_csv Salaries            "${CONTRIB_DIR}/Salaries.csv"
load_csv Schools             "${CONTRIB_DIR}/Schools.csv"

TABLE_COUNT=$(psql -t -A --username "$POSTGRES_USER" --dbname "$LAHMAN_DB" \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")

if [ "$TABLE_COUNT" -lt 27 ]; then
    echo "ERROR: Only ${TABLE_COUNT} tables found after load — expected 27."
    exit 1
fi

touch "$MARKER"
echo "Lahman Baseball Database loaded successfully (${TABLE_COUNT} tables)."
