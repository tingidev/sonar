"""Semantic description generation — LLM-powered meaning inference from schema + samples."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum

from sonar.connectors.postgres import Column, Table
from sonar.engine._prompts import SYSTEM_PROMPT, build_table_prompt
from sonar.engine.llm import LLMClient, LLMConfig

_LOGGER = logging.getLogger("sonar.engine.describe")

_PARSE_RETRY_REMINDER = (
    "Your previous response was not valid JSON. Return only a single JSON object "
    "matching the schema. No prose, no markdown, no code fences."
)

_RAW_TEXT_MAX = 500
_MAX_PROVIDER_RETRIES = 3


class SemanticType(StrEnum):
    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    MEASURE = "measure"
    OTHER = "other"


class PIIRisk(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ColumnDescription:
    name: str
    description: str
    semantic_type: SemanticType
    pii_risk: PIIRisk
    confidence: float


@dataclass(frozen=True)
class TableDescription:
    schema: str
    name: str
    description: str
    grain: str
    domain_hints: tuple[str, ...]
    columns: tuple[ColumnDescription, ...]
    confidence: float


class DescriptionError(Exception):
    """Base class for description-engine errors."""


class DescriptionParseError(DescriptionError):
    """LLM output could not be parsed into a TableDescription after one retry."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text[:_RAW_TEXT_MAX]


def _parse_table_description(
    raw: str,
    schema: str,
    name: str,
    columns: tuple[Column, ...],
) -> TableDescription:
    try:
        payload = json.loads(raw)
        cols_payload = payload["columns"]
        if len(cols_payload) != len(columns):
            raise ValueError(
                f"LLM returned {len(cols_payload)} columns, expected {len(columns)}"
            )
        parsed_columns: list[ColumnDescription] = []
        for i, (source_col, col_payload) in enumerate(
            zip(columns, cols_payload, strict=True)
        ):
            payload_name = col_payload["name"]
            if payload_name != source_col.name:
                raise ValueError(
                    f"LLM returned column name '{payload_name}' at position {i}, "
                    f"expected '{source_col.name}'"
                )
            parsed_columns.append(
                ColumnDescription(
                    name=source_col.name,
                    description=col_payload["description"],
                    semantic_type=SemanticType(col_payload["semantic_type"]),
                    pii_risk=PIIRisk(col_payload["pii_risk"]),
                    confidence=float(col_payload["confidence"]),
                )
            )
        return TableDescription(
            schema=schema,
            name=name,
            description=payload["description"],
            grain=payload["grain"],
            domain_hints=tuple(payload.get("domain_hints", ())),
            columns=tuple(parsed_columns),
            confidence=float(payload["confidence"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise DescriptionParseError(
            f"Could not parse table description: {exc}", raw_text=raw
        ) from exc


class DescriptionEngine:
    """Generates semantic descriptions for tables and columns using an LLMClient."""

    def __init__(self, llm_client: LLMClient, config: LLMConfig | None = None) -> None:
        self._llm = llm_client
        self._config = config or LLMConfig()

    async def describe_table(self, table: Table, samples: list[dict]) -> TableDescription:
        prompt = build_table_prompt(table, samples)
        try:
            raw = await self._llm.generate(prompt, system=SYSTEM_PROMPT)
        except Exception:
            self._log(table, outcome="provider_error")
            raise
        try:
            description = _parse_table_description(raw, table.schema, table.name, table.columns)
            self._log(table, outcome="ok")
            return description
        except DescriptionParseError:
            pass

        retry_prompt = f"{prompt}\n\n{_PARSE_RETRY_REMINDER}"
        try:
            raw_retry = await self._llm.generate(retry_prompt, system=SYSTEM_PROMPT)
        except Exception:
            self._log(table, outcome="provider_error")
            raise
        try:
            description = _parse_table_description(
                raw_retry, table.schema, table.name, table.columns
            )
            self._log(table, outcome="parse_retry")
            return description
        except DescriptionParseError:
            self._log(table, outcome="failed")
            raise

    async def describe_database(
        self,
        tables: list[Table],
        samples_per_table: dict[tuple[str, str], list[dict]],
    ) -> dict[tuple[str, str], TableDescription | None]:
        if not tables:
            return {}

        semaphore = asyncio.Semaphore(self._config.max_concurrent_calls)

        async def _bounded(table: Table) -> TableDescription:
            samples = samples_per_table.get((table.schema, table.name), [])
            last_exc: BaseException | None = None
            for attempt in range(_MAX_PROVIDER_RETRIES):
                async with semaphore:
                    try:
                        return await self.describe_table(table, samples)
                    except DescriptionParseError:
                        raise
                    except Exception as exc:
                        last_exc = exc
                if attempt < _MAX_PROVIDER_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
            raise last_exc  # type: ignore[misc]

        results = await asyncio.gather(
            *(_bounded(t) for t in tables), return_exceptions=True
        )

        out: dict[tuple[str, str], TableDescription | None] = {}
        for table, result in zip(tables, results, strict=True):
            key = (table.schema, table.name)
            if isinstance(result, BaseException):
                out[key] = None
            else:
                out[key] = result
        return out

    @staticmethod
    def _log(table: Table, *, outcome: str) -> None:
        _LOGGER.info(
            "describe_table",
            extra={
                "schema": table.schema,
                "table": table.name,
                "columns_count": len(table.columns),
                "outcome": outcome,
            },
        )
