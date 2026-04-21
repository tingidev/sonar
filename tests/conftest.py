import os

import pytest_asyncio

from sonar.connectors.postgres import PostgresConnector

DEFAULT_TEST_DATABASE_URL = "postgresql://sonar:sonar@localhost:5433/sonar_test"


@pytest_asyncio.fixture(scope="session")
async def connector():
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    async with PostgresConnector(url) as conn:
        yield conn
