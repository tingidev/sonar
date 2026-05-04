"""Live-account Snowflake smoke tests — tagged `@pytest.mark.snowflake_live`.

Skipped by default; run on push-to-main and workflow_dispatch via the
`.github/workflows/snowflake-live.yml` workflow. Each test must be cheap
(single-digit cents) — they hit `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` which is
free and warehouse-only.

Required env vars (any one auth mechanism plus the rest):

- `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`
- `SNOWFLAKE_PASSWORD` OR `SNOWFLAKE_PRIVATE_KEY_PATH` OR `SNOWFLAKE_TOKEN`
- `SNOWFLAKE_WAREHOUSE` (any small warehouse — XS is fine)

Tests skip cleanly when credentials are absent so contributors without an
account can still run `pytest` locally.
"""

from __future__ import annotations

import os

import pytest

from sonar.connectors.snowflake import SnowflakeConnector

_REQUIRED = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE")
_AUTH = ("SNOWFLAKE_PASSWORD", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_TOKEN")

_skip_unless_live = pytest.mark.skipif(
    not all(os.environ.get(v) for v in _REQUIRED)
    or not any(os.environ.get(v) for v in _AUTH),
    reason="live Snowflake credentials not present (set SNOWFLAKE_* env vars)",
)


def _live_kwargs() -> dict[str, str]:
    """Connect kwargs scoped to SNOWFLAKE_SAMPLE_DATA.TPCH_SF1."""
    kwargs: dict[str, str] = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": "SNOWFLAKE_SAMPLE_DATA",
        "schema": "TPCH_SF1",
    }
    for env_var, kwarg in (
        ("SNOWFLAKE_PASSWORD", "password"),
        ("SNOWFLAKE_PRIVATE_KEY_PATH", "private_key_file"),
        ("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "private_key_file_pwd"),
        ("SNOWFLAKE_TOKEN", "token"),
        ("SNOWFLAKE_AUTHENTICATOR", "authenticator"),
        ("SNOWFLAKE_ROLE", "role"),
    ):
        value = os.environ.get(env_var)
        if value:
            kwargs[kwarg] = value
    return kwargs


@pytest.mark.snowflake_live
@_skip_unless_live
class TestLiveSnowflakeSampleData:
    async def test_discover_tables_against_tpch_sf1(self) -> None:
        async with SnowflakeConnector(_live_kwargs()) as c:
            tables = await c.discover_tables()
        names = {t.name for t in tables}
        # TPCH_SF1 ships with 8 tables: CUSTOMER, ORDERS, LINEITEM, PART,
        # SUPPLIER, NATION, REGION, PARTSUPP. Smoke-check at least the canonical
        # core ones rather than fingerprinting all 8 (Snowflake may add).
        assert {"CUSTOMER", "ORDERS", "LINEITEM"}.issubset(names)
        # SNOWFLAKE_SAMPLE_DATA is a shared database — KEY_COLUMN_USAGE is not
        # accessible, so the connector falls back to no-PK discovery. PK flags
        # will all be False; row_count may or may not be available depending on
        # the shared database's INFORMATION_SCHEMA surface.
        customer = next(t for t in tables if t.name == "CUSTOMER")
        assert customer.row_count is None or customer.row_count > 0

    async def test_discover_relationships_graceful_on_shared_db(self) -> None:
        # SNOWFLAKE_SAMPLE_DATA is a shared database — constraint views are not
        # accessible. The connector should return an empty list gracefully rather
        # than raising.
        async with SnowflakeConnector(_live_kwargs()) as c:
            fks = await c.discover_relationships()
            assert isinstance(fks, list)

    async def test_sample_table_returns_coerced_rows(self) -> None:
        async with SnowflakeConnector(_live_kwargs()) as c:
            rows = await c.sample_table("TPCH_SF1", "REGION", limit=2)
        # REGION is the smallest TPCH table (5 rows). 2-row sample is fastest.
        assert 1 <= len(rows) <= 2
        for row in rows:
            for value in row.values():
                # Every coerced value is JSON-serializable-ish: str, int,
                # float, bool, None, or a primitive string fallback.
                assert isinstance(value, (str, int, float, bool, type(None)))
