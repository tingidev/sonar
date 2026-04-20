"""Relationship mapping — FK-based + naming heuristic inference."""


class RelationshipMapper:
    """Maps relationships between tables using foreign keys and naming patterns."""

    async def map_from_foreign_keys(self, foreign_keys) -> list[dict]:
        raise NotImplementedError

    async def infer_from_naming(self, tables) -> list[dict]:
        raise NotImplementedError
