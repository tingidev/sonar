"""Sonar MCP server — 5 core tools for agent data navigation.

Tools:
- sonar/discover: what data sources and tables exist?
- sonar/describe: what does table X mean?
- sonar/relationships: how are tables connected?
- sonar/search: find tables/columns related to a concept
- sonar/sample: show example data from a table
"""


class SonarMCPServer:
    """MCP server exposing the Sonar context index."""

    def __init__(self, context_store):
        self._store = context_store

    async def tool_discover(self) -> dict:
        raise NotImplementedError

    async def tool_describe(self, table: str) -> dict:
        raise NotImplementedError

    async def tool_relationships(self, table: str | None = None) -> dict:
        raise NotImplementedError

    async def tool_search(self, query: str) -> dict:
        raise NotImplementedError

    async def tool_sample(self, table: str, limit: int = 5) -> dict:
        raise NotImplementedError
