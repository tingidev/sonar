"""SQL strings used by the Postgres connector. Kept out of postgres.py for readability."""

# Per design.md §D4. Parameterised on %(schemas)s (a text[]).
# The LEFT JOIN + EXISTS pattern flags primary-key columns without duplicating
# column rows when a column participates in both a PK and another constraint.
# The pg_class join surfaces planner row-count statistics (reltuples) per table;
# see row-count-discovery design.md D1/D3 for source choice and Postgres' -1
# sentinel for "no statistics yet". relkind in ('r','p') keeps the match to
# ordinary and partitioned tables so the join stays one-to-many over columns.
TABLES_AND_COLUMNS = """
SELECT
    c.table_schema AS schema,
    c.table_name AS table_name,
    c.column_name,
    c.ordinal_position,
    c.data_type,
    c.udt_name,
    c.is_nullable,
    c.column_default,
    CASE WHEN kcu.column_name IS NOT NULL THEN true ELSE false END AS is_primary_key,
    pc.reltuples::bigint AS reltuples
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema
 AND t.table_name = c.table_name
LEFT JOIN information_schema.key_column_usage kcu
  ON kcu.table_schema = c.table_schema
 AND kcu.table_name = c.table_name
 AND kcu.column_name = c.column_name
 AND EXISTS (
     SELECT 1
     FROM information_schema.table_constraints tc
     WHERE tc.constraint_name = kcu.constraint_name
       AND tc.constraint_schema = kcu.constraint_schema
       AND tc.constraint_type = 'PRIMARY KEY'
 )
LEFT JOIN pg_namespace pn
  ON pn.nspname = c.table_schema
LEFT JOIN pg_class pc
  ON pc.relname = c.table_name
 AND pc.relnamespace = pn.oid
 AND pc.relkind IN ('r', 'p')
WHERE t.table_type = 'BASE TABLE'
  AND c.table_schema = ANY(%(schemas)s)
ORDER BY c.table_schema, c.table_name, c.ordinal_position;
"""

# Per design.md §D6. The `position_in_unique_constraint` equality is
# load-bearing: it aligns columns of composite foreign keys when the
# referenced column has a different name in the target table.
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
ORDER BY src.table_schema, src.table_name, src.constraint_name, src.ordinal_position;
"""

# Names every user schema (excludes pg_catalog, information_schema, and any
# pg_*-prefixed system schema). Hoisted out of postgres.py so the no-side-effects
# static check covers every SQL string the connector executes.
NON_SYSTEM_SCHEMAS = """
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
  AND schema_name NOT LIKE 'pg\\_%' ESCAPE '\\'
ORDER BY schema_name
"""
