#!/usr/bin/env bash
# Downloads and loads FAERS 2024Q4 ASCII export into a local Postgres instance.
set -euo pipefail

FAERS_URL="https://fis.fda.gov/content/Exports/faers_ascii_2024Q4.zip"
FAERS_DB="faers"
MARKER="/var/lib/postgresql/data/.faers_loaded"

if [ -f "$MARKER" ]; then
    echo "FAERS 2024Q4 already loaded, skipping download."
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading FAERS 2024Q4 (~65 MB)..."
curl -fSL --progress-bar -o "${TMPDIR}/faers.zip" "$FAERS_URL"

echo "Extracting archive..."
unzip -j "${TMPDIR}/faers.zip" "*.txt" -d "${TMPDIR}/data"

echo "Creating database ${FAERS_DB}..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE ${FAERS_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${FAERS_DB}')
    \gexec
SQL

echo "Creating tables..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$FAERS_DB" <<-SQL
    CREATE TABLE demo (
        primaryid varchar, caseid varchar, caseversion varchar, i_f_code varchar,
        event_dt varchar, mfr_dt varchar, init_fda_dt varchar, fda_dt varchar,
        rept_cod varchar, auth_num varchar, mfr_num varchar, mfr_sndr varchar,
        lit_ref varchar, age varchar, age_cod varchar, age_grp varchar,
        sex varchar, e_sub varchar, wt varchar, wt_cod varchar,
        rept_dt varchar, to_mfr varchar, occp_cod varchar,
        reporter_country varchar, occr_country varchar
    );

    CREATE TABLE drug (
        primaryid varchar, caseid varchar, drug_seq varchar, role_cod varchar,
        drugname varchar, prod_ai varchar, val_vbm varchar, route varchar,
        dose_vbm varchar, cum_dose_chr varchar, cum_dose_unit varchar,
        dechal varchar, rechal varchar, lot_num varchar, exp_dt varchar,
        nda_num varchar, dose_amt varchar, dose_unit varchar,
        dose_form varchar, dose_freq varchar
    );

    CREATE TABLE reac (
        primaryid varchar, caseid varchar, pt varchar, drug_rec_act varchar
    );

    CREATE TABLE outc (
        primaryid varchar, caseid varchar, outc_cod varchar
    );

    CREATE TABLE rpsr (
        primaryid varchar, caseid varchar, rpsr_cod varchar
    );

    CREATE TABLE indi (
        primaryid varchar, caseid varchar, indi_drug_seq varchar, indi_pt varchar
    );

    CREATE TABLE ther (
        primaryid varchar, caseid varchar, dsg_drug_seq varchar,
        start_dt varchar, end_dt varchar, dur varchar, dur_cod varchar
    );
SQL

echo "Loading tables..."
DATADIR="${TMPDIR}/data"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$FAERS_DB" \
    -c "COPY demo FROM '${DATADIR}/DEMO24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY drug FROM '${DATADIR}/DRUG24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY reac FROM '${DATADIR}/REAC24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY outc FROM '${DATADIR}/OUTC24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY rpsr FROM '${DATADIR}/RPSR24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY indi FROM '${DATADIR}/INDI24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');" \
    -c "COPY ther FROM '${DATADIR}/THER24Q4.txt' WITH (FORMAT csv, DELIMITER '\$', HEADER true, ENCODING 'LATIN1', NULL '');"

echo "Creating indexes..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$FAERS_DB" <<-SQL
    CREATE INDEX demo_idx ON demo(primaryid, caseid);
    CREATE INDEX drug_idx ON drug(primaryid, caseid);
    CREATE INDEX reac_idx ON reac(primaryid, caseid);
    CREATE INDEX outc_idx ON outc(primaryid, caseid);
    CREATE INDEX rpsr_idx ON rpsr(primaryid, caseid);
    CREATE INDEX indi_idx ON indi(primaryid, caseid);
    CREATE INDEX ther_idx ON ther(primaryid, caseid);
SQL

TABLE_COUNT=$(psql -t -A --username "$POSTGRES_USER" --dbname "$FAERS_DB" \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")

if [ "$TABLE_COUNT" -lt 7 ]; then
    echo "ERROR: Only ${TABLE_COUNT} tables found after load — expected 7."
    exit 1
fi

touch "$MARKER"
echo "FAERS 2024Q4 loaded successfully (${TABLE_COUNT} tables)."
