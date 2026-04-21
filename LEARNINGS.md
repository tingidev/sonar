# Learnings

Technical explanations of what we're building and why, written as we go. Each section maps to a milestone or decision point.

---

## Project Setup

### Why Poetry + src layout?

Poetry manages Python dependencies and packaging. The `src/` layout (as opposed to putting `sonar/` at the root) prevents a common bug: without `src/`, Python can accidentally import from your local source directory instead of the installed package. With `src/`, you must install the package (`poetry install`) before imports work — this guarantees your tests run against the same code a user would install.

### Why async throughout?

Sonar's operations involve I/O-heavy work: database queries, LLM API calls, MCP message handling. Async (`async`/`await`) lets Python handle multiple I/O operations without blocking. When you `await` a database query, Python can start an LLM call in parallel instead of waiting idle. This matters when scanning 50 tables — you could describe multiple tables concurrently rather than sequentially.

We use `psycopg` 3 (not `psycopg2`) specifically because it has native async support.

### Why frozen dataclasses?

```python
@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
```

`frozen=True` means instances can't be modified after creation. If you want a different value, you create a new object. This eliminates an entire class of bugs where something accidentally modifies shared state. It also makes objects hashable (usable as dict keys or in sets) for free.

The tradeoff: slightly more memory (new objects instead of modifying in place). Irrelevant at our scale.

---

## Postgres Connector

The first real capability. Scans a live Postgres database and returns its structure as Python objects: `Table`s with their `Column`s, `ForeignKey`s between them, and small row samples for each table. Downstream stages (LLM descriptions, relationship graphs, MCP context) all feed off this output. The non-obvious parts:

### `information_schema` vs `pg_catalog`

Postgres exposes schema metadata two ways. `pg_catalog` is Postgres's own internal bookkeeping — fast, complete, and vendor-specific. `information_schema` is the SQL-standard view over `pg_catalog` — portable, slightly slower, and **permission-scoped**: it silently hides tables the connecting role lacks `USAGE` on.

We chose `information_schema`. For the test DB the connecting role owns everything, so visibility is complete. For real deployments this becomes a concern the `mcp-server` change will handle. The tradeoff is deliberate: portability and a standard mental model now, re-inspection when we know the permission shape of real customer DBs.

### psycopg3 row factories (`dict_row`)

psycopg3 returns tuples by default — positional access, column order matters to the caller. The `row_factory` argument lets you pick a different shape. We use `psycopg.rows.dict_row` everywhere introspection runs, so the cursor yields `dict[str, Any]`. Two payoffs:

1. The `discover_tables` grouping loop refers to columns by name (`row["table_name"]`), not index — the query can evolve without rewriting the Python.
2. `sample_table` returns what downstream code wants anyway: dicts keyed by column name, ready to `json.dumps`.

The `dict_row` factory is set per-cursor, not per-connection, so cursors that don't need it stay on the default tuple shape.

### The `udt_name` fallback for ARRAY and USER-DEFINED

`information_schema.columns.data_type` is the SQL-standard name, which for non-standard types is useless: it returns the literal string `"ARRAY"` for any array, and `"USER-DEFINED"` for any enum or domain. An LLM reading `"data_type: ARRAY"` learns nothing — array of what?

The companion column `udt_name` is Postgres's own type name: `_text` for `text[]`, `_int4` for `integer[]`, or the enum/domain's own name (e.g. `order_status`). We swap to `udt_name` exactly when `data_type` is `ARRAY` or `USER-DEFINED`, and leave the standard name alone otherwise. The result: the LLM sees `uuid`, `timestamp with time zone`, `numeric`, `_text`, `order_status` — all informative.

### The `position_in_unique_constraint` join for composite FKs

The query shape people write first is to join `referential_constraints` → `key_column_usage` twice (source side, target side) on `(constraint_name, schema)` and then align source and target columns by name. That works for simple FKs where the referenced column has the same name in both tables. It breaks for composite FKs where the names differ.

The correct join uses `position_in_unique_constraint`: every row in `key_column_usage` for a referencing column carries an integer saying "this is the Nth column of the target's unique constraint". Join the target side on `ordinal_position = position_in_unique_constraint` and the alignment is positional, not nominal. A two-column FK `(a, b) → (x, y)` produces two rows: `a → x`, `b → y`, correct regardless of column names.

### `psycopg.sql.Identifier` even for trusted inputs

`sample_table` takes `schema` and `table` strings and builds `SELECT * FROM {schema}.{table} LIMIT {n}`. In our flow both come from prior discovery, so they are trusted — no untrusted-input SQL injection concern. We still compose the query with `psycopg.sql.Identifier(schema)` and `psycopg.sql.Identifier(table)` rather than f-string interpolation.

Why: `Identifier` handles reserved words (`SELECT * FROM "order"`), mixed case (`"MyTable"`), and embedded quotes for free. It costs nothing and removes an entire class of "works on dev schema, breaks on customer schema" bugs. "Trusted input" is not a license to skip identifier quoting — it's a license to skip **parameter** quoting, which is a different mechanism.

`psycopg.sql.Literal(limit)` is used for the integer limit for the same reason: belt-and-braces composition via a library that knows every corner case, instead of `f"LIMIT {limit}"`.

### Async for a one-shot scan

Sonar scans a database once per invocation. There's no long-running server, no request/response fan-out, no concurrent queries within a single scan. So why is the connector async?

Because the upstream consumer is async. The LLM description stage will call Anthropic's API per table; the MCP server is async by protocol; the context index will hydrate from async I/O. Making the connector sync would force a `asyncio.run()` wrapper at every caller, or split the codebase into a sync island around the connector. Async costs us almost nothing here — a single async context manager, no connection pool, no concurrency within the connector itself — and keeps the whole pipeline in one event loop.

`psycopg3` (unlike `psycopg2`) has native async support, so this choice is free.

### Connection lifecycle as async context manager

`PostgresConnector` is used as `async with PostgresConnector(url) as conn: ...`. `__aenter__` opens one `AsyncConnection`; `__aexit__` closes it. Public methods raise `RuntimeError` if called outside the context.

Alternatives considered and rejected:

- **Per-method connect/disconnect.** Cheap-looking but wrong: every test that exercises three methods pays three handshakes. Also leaks the concept of "connection" into every caller.
- **Connection pool.** Overkill. A scan is a short serial sequence of three queries; there is never a second concurrent query.
- **Single connection opened in `__init__`.** The class now owns a resource with no explicit close point. Exceptions during construction or between method calls leak connections. Async `__init__` is also impossible — you'd need a factory `classmethod` and the syntax becomes uglier than the context manager version it was trying to avoid.

The chosen shape makes the resource lifetime visible at the call site, which is exactly where the reader needs it.

---
