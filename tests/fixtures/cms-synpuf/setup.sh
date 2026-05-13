#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_FILE="${SCRIPT_DIR}/cms_synpuf.duckdb"

if [ -f "$DB_FILE" ]; then
    echo "CMS SynPUF database already exists at ${DB_FILE}, skipping."
    exit 0
fi

command -v duckdb >/dev/null 2>&1 || { echo "duckdb CLI required. Install with: brew install duckdb"; exit 1; }
command -v unzip >/dev/null 2>&1 || { echo "unzip required. Install with: brew install unzip"; exit 1; }

cd "$SCRIPT_DIR"

BASE_CMS="https://www.cms.gov/Research-Statistics-Data-and-Systems/Downloadable-Public-Use-Files/SynPUFs/Downloads"
BASE_DL="https://downloads.cms.gov/files"

download_if_missing() {
    local out="$1"
    local url="$2"
    if [ -f "$out" ]; then
        echo "  $out already downloaded, skipping."
        return
    fi
    echo "  Downloading ${out}..."
    curl -fSL --progress-bar -o "$out" "$url"
}

echo "Downloading CMS DE-SynPUF Sample 1 (~250 MB)..."
echo "Note: 2010 beneficiary file is skipped — CMS has a known bug where that link points to the wrong sample."

download_if_missing "DE1_0_2008_Beneficiary_Summary_File_Sample_1.zip" "${BASE_CMS}/DE1_0_2008_Beneficiary_Summary_File_Sample_1.zip"
download_if_missing "DE1_0_2009_Beneficiary_Summary_File_Sample_1.zip" "${BASE_CMS}/DE1_0_2009_Beneficiary_Summary_File_Sample_1.zip"
download_if_missing "DE1_0_2008_to_2010_Carrier_Claims_Sample_1A.zip" "${BASE_DL}/DE1_0_2008_to_2010_Carrier_Claims_Sample_1A.zip"
download_if_missing "DE1_0_2008_to_2010_Carrier_Claims_Sample_1B.zip" "${BASE_DL}/DE1_0_2008_to_2010_Carrier_Claims_Sample_1B.zip"
download_if_missing "DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.zip" "${BASE_CMS}/DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.zip"
download_if_missing "DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.zip" "${BASE_CMS}/DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.zip"
download_if_missing "DE1_0_2008_to_2010_Prescription_Drug_Events_Sample_1.zip" "${BASE_DL}/DE1_0_2008_to_2010_Prescription_Drug_Events_Sample_1.zip"

echo "Extracting archives..."
for zip in *.zip; do
    echo "  Extracting ${zip}..."
    unzip -o "$zip"
done

echo "Loading into DuckDB..."
cat > create_tables.sql <<'EOF'
CREATE TABLE beneficiary AS
SELECT 2008 AS bene_year, * FROM read_csv('DE1_0_2008_Beneficiary_Summary_File_Sample_1.csv', header=true, nullstr='', all_varchar=true)
UNION ALL
SELECT 2009 AS bene_year, * FROM read_csv('DE1_0_2009_Beneficiary_Summary_File_Sample_1.csv', header=true, nullstr='', all_varchar=true);

CREATE TABLE carrier AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Carrier_Claims_Sample_1A.csv', header=true, nullstr='', all_varchar=true)
UNION ALL
SELECT * FROM read_csv('DE1_0_2008_to_2010_Carrier_Claims_Sample_1B.csv', header=true, nullstr='', all_varchar=true);

CREATE TABLE inpatient AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.csv', header=true, nullstr='', all_varchar=true);

CREATE TABLE outpatient AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.csv', header=true, nullstr='', all_varchar=true);

CREATE TABLE pde AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Prescription_Drug_Events_Sample_1.csv', header=true, nullstr='', all_varchar=true);
EOF

duckdb "$DB_FILE" < create_tables.sql

echo "Cleaning up downloaded files..."
rm -f *.zip *.csv create_tables.sql

echo "CMS SynPUF loaded successfully to ${DB_FILE}"
