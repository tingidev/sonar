"""Semantic description generation — LLM-powered meaning inference from schema + samples."""


class DescriptionEngine:
    """Generates semantic descriptions for tables and columns using LLM."""

    def __init__(self, llm_client):
        self._llm = llm_client

    async def describe_table(self, table, samples: list[dict]) -> dict:
        raise NotImplementedError

    async def describe_database(self, tables, samples_per_table: dict) -> dict:
        raise NotImplementedError
