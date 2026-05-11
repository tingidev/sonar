"""SQL strings used by the BigQuery connector.

Per-dataset FK/PK INFORMATION_SCHEMA queries (per design.md D5). The dataset-
scoped form is used (not the regional `project.region-X.INFORMATION_SCHEMA.*`
meta-table form) so we never have to detect dataset regions.
"""


def _backtick(name: str) -> str:
    # Local quoter — module-private to keep this file dependency-free.
    # Mirrors `bigquery._bq_quote` but lives here so the SQL builder is
    # importable without bringing in the connector module.
    if "\x00" in name:
        raise ValueError(f"identifier contains null byte: {name!r}")
    escaped = name.replace("`", "\\`")
    return f"`{escaped}`"


def constraints_query(project: str, dataset: str) -> str:
    """FK + PK constraints for a single dataset, joined into one result set.

    Returns rows with the constraint_type discriminator, source columns, and
    target columns (NULL for PK rows; populated for FK rows).
    """
    qproject = _backtick(project)
    qdataset = _backtick(dataset)
    return f"""
SELECT
    tc.constraint_type            AS constraint_type,
    kcu.table_name                AS source_table,
    kcu.column_name               AS source_column,
    rcu.table_schema              AS target_schema,
    rcu.table_name                AS target_table,
    rcu.column_name               AS target_column
FROM {qproject}.{qdataset}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN {qproject}.{qdataset}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
  ON kcu.constraint_name   = tc.constraint_name
 AND kcu.constraint_schema = tc.constraint_schema
LEFT JOIN {qproject}.{qdataset}.INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
  ON rc.constraint_name    = tc.constraint_name
 AND rc.constraint_schema  = tc.constraint_schema
LEFT JOIN {qproject}.{qdataset}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE rcu
  ON rcu.constraint_name   = rc.unique_constraint_name
 AND rcu.constraint_schema = rc.unique_constraint_schema
 AND rcu.ordinal_position  = kcu.position_in_unique_constraint
WHERE tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
ORDER BY tc.constraint_type, kcu.table_name, kcu.ordinal_position
"""
