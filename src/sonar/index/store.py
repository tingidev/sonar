"""JSON-based context store. Reads/writes the ContextBundle to disk."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path

from sonar.connectors.postgres import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import (
    SCHEMA_VERSION,
    BundleIntegrityError,
    BundleMeta,
    BundleVersionError,
    ContextBundle,
)
from sonar.relationships import Relationship, RelationshipKind

_LOGGER = logging.getLogger("sonar.index")

_META_FILE = "meta.json"
_TABLES_FILE = "tables.json"
_DESCRIPTIONS_FILE = "descriptions.json"
_RELATIONSHIPS_FILE = "relationships.json"


def _json_default(obj: object) -> object:
    if isinstance(obj, StrEnum):
        return obj.value
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _dump_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, default=_json_default, indent=2, sort_keys=False)


def _load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class ContextStore:
    """Persists a ContextBundle as four per-capability JSON files under `bundle_dir`."""

    def __init__(self, bundle_dir: Path) -> None:
        self._dir = Path(bundle_dir)

    @property
    def bundle_dir(self) -> Path:
        return self._dir

    def write(self, bundle: ContextBundle) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

        _dump_json(self._dir / _META_FILE, asdict(bundle.meta))
        _dump_json(self._dir / _TABLES_FILE, [asdict(t) for t in bundle.tables])
        _dump_json(
            self._dir / _DESCRIPTIONS_FILE,
            _encode_descriptions(bundle.descriptions),
        )
        _dump_json(
            self._dir / _RELATIONSHIPS_FILE,
            [asdict(r) for r in bundle.relationships],
        )

        _LOGGER.info("context_bundle_write", extra=_bundle_log_extra(bundle))

    def read(self) -> ContextBundle | None:
        if not self._dir.exists():
            return None
        meta_path = self._dir / _META_FILE
        if not meta_path.exists():
            return None

        meta_raw = _load_json(meta_path)
        if not isinstance(meta_raw, dict):
            raise BundleIntegrityError(f"{_META_FILE} is not a JSON object")
        version = int(meta_raw.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise BundleVersionError(expected=SCHEMA_VERSION, found=version)

        meta = BundleMeta(
            schema_version=version,
            generated_at=str(meta_raw["generated_at"]),
            connector=str(meta_raw["connector"]),
            database=str(meta_raw["database"]),
        )

        tables_raw = _load_json(self._dir / _TABLES_FILE)
        if not isinstance(tables_raw, list):
            raise BundleIntegrityError(f"{_TABLES_FILE} is not a JSON array")
        tables = tuple(_decode_table(t) for t in tables_raw)

        descriptions_raw = _load_json(self._dir / _DESCRIPTIONS_FILE)
        if not isinstance(descriptions_raw, dict):
            raise BundleIntegrityError(f"{_DESCRIPTIONS_FILE} is not a JSON object")
        descriptions = _decode_descriptions(descriptions_raw)

        relationships_raw = _load_json(self._dir / _RELATIONSHIPS_FILE)
        if not isinstance(relationships_raw, list):
            raise BundleIntegrityError(f"{_RELATIONSHIPS_FILE} is not a JSON array")
        relationships = tuple(_decode_relationship(r) for r in relationships_raw)

        _check_integrity(tables, descriptions)

        bundle = ContextBundle(
            meta=meta,
            tables=tables,
            descriptions=descriptions,
            relationships=relationships,
        )

        _LOGGER.info("context_bundle_read", extra=_bundle_log_extra(bundle))
        return bundle


def _bundle_log_extra(bundle: ContextBundle) -> dict[str, int]:
    present = sum(1 for v in bundle.descriptions.values() if v is not None)
    null = sum(1 for v in bundle.descriptions.values() if v is None)
    return {
        "tables": len(bundle.tables),
        "descriptions_present": present,
        "descriptions_null": null,
        "relationships": len(bundle.relationships),
    }


def _encode_descriptions(
    descriptions: dict[tuple[str, str], TableDescription | None],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for (schema, name), value in descriptions.items():
        key = f"{schema}.{name}"
        out[key] = None if value is None else asdict(value)
    return out


def _decode_descriptions(
    raw: dict[str, object],
) -> dict[tuple[str, str], TableDescription | None]:
    out: dict[tuple[str, str], TableDescription | None] = {}
    for key, value in raw.items():
        schema, sep, name = key.partition(".")
        if not sep or not schema or not name:
            raise BundleIntegrityError(f"Malformed description key: {key!r}")
        if "." in name:
            raise BundleIntegrityError(f"Description key {key!r} contains a dotted table name")
        tkey = (schema, name)
        if value is None:
            out[tkey] = None
        else:
            if not isinstance(value, dict):
                raise BundleIntegrityError(f"Description for {key!r} is not a JSON object")
            out[tkey] = _decode_table_description(value)
    return out


def _decode_table(raw: dict) -> Table:
    return Table(
        schema=raw["schema"],
        name=raw["name"],
        columns=tuple(_decode_column(c) for c in raw["columns"]),
        row_count=raw.get("row_count"),
    )


def _decode_column(raw: dict) -> Column:
    return Column(
        name=raw["name"],
        data_type=raw["data_type"],
        nullable=raw["nullable"],
        is_primary_key=raw["is_primary_key"],
        foreign_key=raw.get("foreign_key"),
        default=raw.get("default"),
    )


def _decode_table_description(raw: dict) -> TableDescription:
    return TableDescription(
        schema=raw["schema"],
        name=raw["name"],
        description=raw["description"],
        grain=raw["grain"],
        domain_hints=tuple(raw.get("domain_hints", ())),
        columns=tuple(_decode_column_description(c) for c in raw["columns"]),
        confidence=float(raw["confidence"]),
    )


def _decode_column_description(raw: dict) -> ColumnDescription:
    return ColumnDescription(
        name=raw["name"],
        description=raw["description"],
        semantic_type=SemanticType(raw["semantic_type"]),
        pii_risk=PIIRisk(raw["pii_risk"]),
        confidence=float(raw["confidence"]),
    )


def _decode_relationship(raw: dict) -> Relationship:
    return Relationship(
        source_schema=raw["source_schema"],
        source_table=raw["source_table"],
        source_column=raw["source_column"],
        target_schema=raw["target_schema"],
        target_table=raw["target_table"],
        target_column=raw["target_column"],
        kind=RelationshipKind(raw["kind"]),
    )


def _check_integrity(
    tables: tuple[Table, ...],
    descriptions: dict[tuple[str, str], TableDescription | None],
) -> None:
    table_keys = {(t.schema, t.name) for t in tables}
    desc_keys = set(descriptions.keys())
    orphan = desc_keys - table_keys
    missing = table_keys - desc_keys
    if orphan:
        raise BundleIntegrityError(f"Description keys without a matching table: {sorted(orphan)}")
    if missing:
        raise BundleIntegrityError(f"Tables without a description entry: {sorted(missing)}")
