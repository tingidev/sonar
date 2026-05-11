"""SQL strings used by the DuckDB connector."""

# Lists user-visible schemas, excluding DuckDB system schemas.
# DISTINCT required — information_schema.schemata returns duplicate rows for
# the same schema in DuckDB 1.x (one per catalog entry).
NON_SYSTEM_SCHEMAS = """
SELECT DISTINCT schema_name
FROM information_schema.schemata
WHERE schema_name NOT IN ('information_schema', 'pg_catalog')
ORDER BY schema_name
"""


# Tables, columns, PK flags, and per-table row count. The {schemas_placeholder}
# is filled at query-build time with the right number of ? positional parameters
# (same pattern as the Snowflake connector's %s approach). Row counts come from
# duckdb_tables().estimated_size, which DuckDB updates eagerly.
TABLES_AND_COLUMNS_TEMPLATE = """
SELECT
    c.table_schema      AS schema,
    c.table_name        AS table_name,
    c.column_name       AS column_name,
    c.ordinal_position  AS ordinal_position,
    c.data_type         AS data_type,
    c.is_nullable       AS is_nullable,
    c.column_default    AS column_default,
    CASE WHEN kcu.column_name IS NOT NULL THEN TRUE ELSE FALSE END AS is_primary_key,
    dt.estimated_size   AS row_count
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema
 AND t.table_name   = c.table_name
LEFT JOIN information_schema.key_column_usage kcu
  ON kcu.table_schema = c.table_schema
 AND kcu.table_name   = c.table_name
 AND kcu.column_name  = c.column_name
 AND EXISTS (
     SELECT 1
     FROM information_schema.table_constraints tc
     WHERE tc.constraint_name   = kcu.constraint_name
       AND tc.constraint_schema = kcu.constraint_schema
       AND tc.constraint_type   = 'PRIMARY KEY'
 )
LEFT JOIN duckdb_tables() dt
  ON dt.schema_name = c.table_schema
 AND dt.table_name  = c.table_name
WHERE t.table_type = 'BASE TABLE'
  AND c.table_schema IN ({schemas_placeholder})
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


def tables_and_columns_query(schema_count: int) -> str:
    """Return the TABLES_AND_COLUMNS query with `schema_count` ? placeholders."""
    if schema_count <= 0:
        raise ValueError("schema_count must be positive")
    placeholders = ", ".join(["?"] * schema_count)
    return TABLES_AND_COLUMNS_TEMPLATE.format(schemas_placeholder=placeholders)


# Foreign keys via standard INFORMATION_SCHEMA. DuckDB files are single-catalog,
# so no cross-database FK filtering is needed (per design.md D6).
FOREIGN_KEYS = """
SELECT
    src.table_schema  AS source_schema,
    src.table_name    AS source_table,
    src.column_name   AS source_column,
    tgt.table_schema  AS target_schema,
    tgt.table_name    AS target_table,
    tgt.column_name   AS target_column
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage src
  ON src.constraint_name   = rc.constraint_name
 AND src.constraint_schema = rc.constraint_schema
JOIN information_schema.key_column_usage tgt
  ON tgt.constraint_name   = rc.unique_constraint_name
 AND tgt.constraint_schema = rc.unique_constraint_schema
 AND tgt.ordinal_position  = src.position_in_unique_constraint
ORDER BY src.table_schema, src.table_name, src.constraint_name, src.ordinal_position
"""
