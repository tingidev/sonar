"""JSON-based context store. Reads/writes the context index to disk."""

from pathlib import Path


DEFAULT_INDEX_DIR = Path(".sonar")


class ContextStore:
    """Persists and retrieves the context index as JSON files."""

    def __init__(self, index_dir: Path | None = None):
        self._dir = index_dir or DEFAULT_INDEX_DIR

    def save(self, context: dict) -> Path:
        raise NotImplementedError

    def load(self) -> dict | None:
        raise NotImplementedError

    def exists(self) -> bool:
        return self._dir.exists()
