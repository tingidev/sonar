"""SQL strings used by the Snowflake connector.

Queries are scoped to the connector's bound database (per design.md D2);
INFORMATION_SCHEMA in Snowflake is per-database, so binding at connect()
time naturally limits visibility to the right scope.
"""

# Lists user-visible schemas in the bound database, excluding INFORMATION_SCHEMA.
NON_SYSTEM_SCHEMAS = """
SELECT SCHEMA_NAME
FROM INFORMATION_SCHEMA.SCHEMATA
WHERE SCHEMA_NAME <> 'INFORMATION_SCHEMA'
ORDER BY SCHEMA_NAME
"""


# Tables, columns, PK flags, and per-table ROW_COUNT (per design.md D7) for the
# bound database. The IN clause is built dynamically with %s placeholders so
# the schema names are still parameterised through the driver, not interpolated.
# ROW_COUNT lives on INFORMATION_SCHEMA.TABLES — same int|None contract as
# Postgres' pg_class.reltuples. The expression is templated because fakesnow
# does not expose ROW_COUNT (a known permissive-SQL gap, see design.md D6); the
# connector probes for column availability at connect time and substitutes
# CAST(NULL AS BIGINT) when missing.
TABLES_AND_COLUMNS_TEMPLATE = """
SELECT
    c.TABLE_SCHEMA      AS schema,
    c.TABLE_NAME        AS table_name,
    c.COLUMN_NAME       AS column_name,
    c.ORDINAL_POSITION  AS ordinal_position,
    c.DATA_TYPE         AS data_type,
    c.IS_NULLABLE       AS is_nullable,
    c.COLUMN_DEFAULT    AS column_default,
    CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN TRUE ELSE FALSE END AS is_primary_key,
    {row_count_expr}    AS row_count
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
 AND t.TABLE_NAME   = c.TABLE_NAME
LEFT JOIN (
    SELECT
        kcu.TABLE_SCHEMA,
        kcu.TABLE_NAME,
        kcu.COLUMN_NAME
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
      ON kcu.CONSTRAINT_NAME   = tc.CONSTRAINT_NAME
     AND kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
    WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
) pk
  ON pk.TABLE_SCHEMA = c.TABLE_SCHEMA
 AND pk.TABLE_NAME   = c.TABLE_NAME
 AND pk.COLUMN_NAME  = c.COLUMN_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND c.TABLE_SCHEMA IN ({schemas_placeholder})
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""


# Probe for whether INFORMATION_SCHEMA.TABLES exposes ROW_COUNT. Real Snowflake
# returns 1; fakesnow returns 0. Result drives the row_count_expr substitution
# in TABLES_AND_COLUMNS_TEMPLATE so callers always get an int|None.
ROW_COUNT_AVAILABLE_PROBE = """
SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'INFORMATION_SCHEMA'
  AND TABLE_NAME = 'TABLES'
  AND COLUMN_NAME = 'ROW_COUNT'
"""


# Foreign keys for the bound database. UNIQUE_CONSTRAINT_CATALOG carries the
# target database name; cross-database FKs are detected and dropped in Python
# (per design.md D2). The LEFT JOIN to KEY_COLUMN_USAGE on the target side
# returns NULL endpoints for cross-database FKs because INFORMATION_SCHEMA is
# per-database — that's the second signal we use to confirm the drop.
FOREIGN_KEYS = """
SELECT
    src.TABLE_SCHEMA               AS source_schema,
    src.TABLE_NAME                 AS source_table,
    src.COLUMN_NAME                AS source_column,
    rc.UNIQUE_CONSTRAINT_CATALOG   AS target_database,
    tgt.TABLE_SCHEMA               AS target_schema,
    tgt.TABLE_NAME                 AS target_table,
    tgt.COLUMN_NAME                AS target_column
FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE src
  ON src.CONSTRAINT_NAME   = rc.CONSTRAINT_NAME
 AND src.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE tgt
  ON tgt.CONSTRAINT_NAME   = rc.UNIQUE_CONSTRAINT_NAME
 AND tgt.CONSTRAINT_SCHEMA = rc.UNIQUE_CONSTRAINT_SCHEMA
 AND tgt.ORDINAL_POSITION  = src.POSITION_IN_UNIQUE_CONSTRAINT
ORDER BY src.TABLE_SCHEMA, src.TABLE_NAME, src.CONSTRAINT_NAME, src.ORDINAL_POSITION
"""


# Fallback for shared/imported databases where KEY_COLUMN_USAGE and
# TABLE_CONSTRAINTS are not accessible. Same shape minus PK detection.
TABLES_AND_COLUMNS_NO_PK_TEMPLATE = """
SELECT
    c.TABLE_SCHEMA      AS schema,
    c.TABLE_NAME        AS table_name,
    c.COLUMN_NAME       AS column_name,
    c.ORDINAL_POSITION  AS ordinal_position,
    c.DATA_TYPE         AS data_type,
    c.IS_NULLABLE       AS is_nullable,
    c.COLUMN_DEFAULT    AS column_default,
    FALSE               AS is_primary_key,
    {row_count_expr}    AS row_count
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
 AND t.TABLE_NAME   = c.TABLE_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND c.TABLE_SCHEMA IN ({schemas_placeholder})
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""


def tables_and_columns_query(
    schema_count: int, *, has_row_count: bool, has_pk_views: bool = True
) -> str:
    """Return the TABLES_AND_COLUMNS query with `schema_count` placeholders.

    `has_row_count=False` substitutes a NULL literal for the ROW_COUNT column,
    which is what fakesnow needs (real Snowflake exposes the column).
    `has_pk_views=False` drops the PK join for shared/imported databases where
    KEY_COLUMN_USAGE is not accessible.
    """
    if schema_count <= 0:
        raise ValueError("schema_count must be positive")
    placeholders = ", ".join(["%s"] * schema_count)
    row_count_expr = "t.ROW_COUNT" if has_row_count else "CAST(NULL AS BIGINT)"
    template = TABLES_AND_COLUMNS_TEMPLATE if has_pk_views else TABLES_AND_COLUMNS_NO_PK_TEMPLATE
    return template.format(
        schemas_placeholder=placeholders,
        row_count_expr=row_count_expr,
    )
