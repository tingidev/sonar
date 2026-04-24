"""Unit tests for the description engine."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any

import pytest

from sonar.connectors.postgres import Column, Table
from sonar.engine._prompts import build_table_prompt
from sonar.engine.describe import (
    ColumnDescription,
    DescriptionEngine,
    DescriptionParseError,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.engine.llm import LLMClient, LLMConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _users_table() -> Table:
    return Table(
        schema="public",
        name="users",
        columns=(
            Column(name="user_id", data_type="uuid", nullable=False, is_primary_key=True),
            Column(name="email", data_type="text", nullable=False, is_primary_key=False),
            Column(
                name="created_at",
                data_type="timestamptz",
                nullable=False,
                is_primary_key=False,
            ),
        ),
    )


def _users_samples() -> list[dict]:
    return [
        {"user_id": "11111111-1111-1111-1111-111111111111", "email": "a@x.com",
         "created_at": "2026-01-01T00:00:00Z"},
        {"user_id": "22222222-2222-2222-2222-222222222222", "email": "b@x.com",
         "created_at": "2026-01-02T00:00:00Z"},
    ]


def _valid_payload_for(table: Table, *, pii: dict[str, str] | None = None) -> str:
    pii = pii or {}
    columns_payload: list[dict[str, Any]] = []
    for col in table.columns:
        semantic = "identifier" if col.is_primary_key else "dimension"
        columns_payload.append(
            {
                "name": col.name,
                "description": f"Column {col.name}",
                "semantic_type": semantic,
                "pii_risk": pii.get(col.name, "none"),
                "confidence": 0.8,
            }
        )
    return json.dumps(
        {
            "description": f"Fixture table {table.name}",
            "grain": f"one row per {table.name[:-1]}",
            "domain_hints": ["test"],
            "columns": columns_payload,
            "confidence": 0.9,
        }
    )


class FakeLLMClient(LLMClient):
    """Records calls, returns scripted responses. Tracks concurrent in-flight peak."""

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        response_for: Any = None,
        delay: float = 0.0,
    ) -> None:
        self._responses = list(responses) if responses is not None else None
        self._response_for = response_for
        self._delay = delay
        self.calls: list[tuple[str, str | None]] = []
        self.peak_concurrent = 0
        self._in_flight = 0
        self._lock = asyncio.Lock()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        async with self._lock:
            self._in_flight += 1
            if self._in_flight > self.peak_concurrent:
                self.peak_concurrent = self._in_flight
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            self.calls.append((prompt, system))
            if self._responses is not None:
                value = self._responses.pop(0)
            elif callable(self._response_for):
                value = self._response_for(prompt, system)
            else:
                value = self._response_for
            if isinstance(value, BaseException):
                raise value
            return value
        finally:
            async with self._lock:
                self._in_flight -= 1


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_table_description_is_frozen(self) -> None:
        td = TableDescription(
            schema="public",
            name="t",
            description="d",
            grain="g",
            domain_hints=(),
            columns=(),
            confidence=0.5,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            td.name = "other"  # type: ignore[misc]

    def test_columns_field_is_tuple(self) -> None:
        td = TableDescription(
            schema="public",
            name="t",
            description="d",
            grain="g",
            domain_hints=(),
            columns=(
                ColumnDescription(
                    name="x",
                    description="x",
                    semantic_type=SemanticType.IDENTIFIER,
                    pii_risk=PIIRisk.NONE,
                    confidence=0.9,
                ),
            ),
            confidence=0.9,
        )
        assert isinstance(td.columns, tuple)
        assert isinstance(td.domain_hints, tuple)

    def test_enum_values_round_trip_through_json(self) -> None:
        payload = {
            "semantic_type": SemanticType.MEASURE,
            "pii_risk": PIIRisk.HIGH,
        }
        serialised = json.dumps(payload)
        loaded = json.loads(serialised)
        assert loaded["semantic_type"] == "measure"
        assert loaded["pii_risk"] == "high"
        assert SemanticType(loaded["semantic_type"]) is SemanticType.MEASURE
        assert PIIRisk(loaded["pii_risk"]) is PIIRisk.HIGH

    def test_pii_risk_medium_roundtrip(self) -> None:
        assert PIIRisk("medium") is PIIRisk.MEDIUM
        assert PIIRisk.MEDIUM.value == "medium"
        column = ColumnDescription(
            name="city",
            description="City of residence",
            semantic_type=SemanticType.DIMENSION,
            pii_risk=PIIRisk.MEDIUM,
            confidence=0.6,
        )
        serialised = json.dumps(dataclasses.asdict(column), default=str)
        loaded = json.loads(serialised)
        assert loaded["pii_risk"] == "medium"
        assert PIIRisk(loaded["pii_risk"]) is PIIRisk.MEDIUM


# ---------------------------------------------------------------------------
# describe_table — happy path
# ---------------------------------------------------------------------------


class TestDescribeTable:
    @pytest.mark.asyncio
    async def test_successful_parse(self) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=[_valid_payload_for(table)])
        engine = DescriptionEngine(client)

        result = await engine.describe_table(table, _users_samples())

        assert isinstance(result, TableDescription)
        assert result.schema == "public"
        assert result.name == "users"
        assert tuple(c.name for c in result.columns) == tuple(c.name for c in table.columns)
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_prompt_composition(self) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=[_valid_payload_for(table)])
        engine = DescriptionEngine(client)

        await engine.describe_table(table, _users_samples())

        prompt, system = client.calls[0]
        assert system is not None
        assert "public.users" in prompt
        for col in table.columns:
            assert f"{col.name}: {col.data_type}" in prompt
        assert "a@x.com" in prompt
        for value in ("identifier", "dimension", "measure", "other"):
            assert value in prompt
        for value in ("none", "low", "medium", "high"):
            assert value in prompt

    @pytest.mark.asyncio
    async def test_pii_classification_respected(self) -> None:
        table = Table(
            schema="public",
            name="people",
            columns=(
                Column("email", "text", False, False),
                Column("ssn", "text", False, False),
                Column("country", "text", False, False),
            ),
        )
        pii = {"email": "high", "ssn": "high", "country": "low"}
        client = FakeLLMClient(responses=[_valid_payload_for(table, pii=pii)])
        engine = DescriptionEngine(client)

        result = await engine.describe_table(table, [])

        by_name = {c.name: c for c in result.columns}
        assert by_name["email"].pii_risk is PIIRisk.HIGH
        assert by_name["ssn"].pii_risk is PIIRisk.HIGH
        assert by_name["country"].pii_risk is PIIRisk.LOW

    @pytest.mark.asyncio
    async def test_pii_classification_medium(self) -> None:
        table = Table(
            schema="public",
            name="people",
            columns=(
                Column("city", "text", False, False),
                Column("ip_address", "inet", False, False),
            ),
        )
        pii = {"city": "medium", "ip_address": "medium"}
        client = FakeLLMClient(responses=[_valid_payload_for(table, pii=pii)])
        engine = DescriptionEngine(client)

        result = await engine.describe_table(table, [])

        by_name = {c.name: c for c in result.columns}
        assert by_name["city"].pii_risk is PIIRisk.MEDIUM
        assert by_name["ip_address"].pii_risk is PIIRisk.MEDIUM


# ---------------------------------------------------------------------------
# describe_table — parse retry
# ---------------------------------------------------------------------------


class TestParseRetry:
    @pytest.mark.asyncio
    async def test_retry_recovers(self) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=["not json {", _valid_payload_for(table)])
        engine = DescriptionEngine(client)

        result = await engine.describe_table(table, _users_samples())

        assert isinstance(result, TableDescription)
        assert len(client.calls) == 2
        second_prompt, _ = client.calls[1]
        assert "not valid JSON" in second_prompt

    @pytest.mark.asyncio
    async def test_permanent_failure_raises(self) -> None:
        table = _users_table()
        bad_1 = "not json {"
        bad_2 = "also not json " + ("x" * 600)
        client = FakeLLMClient(responses=[bad_1, bad_2])
        engine = DescriptionEngine(client)

        with pytest.raises(DescriptionParseError) as excinfo:
            await engine.describe_table(table, _users_samples())

        assert len(client.calls) == 2
        assert hasattr(excinfo.value, "raw_text")
        assert len(excinfo.value.raw_text) <= 500

    @pytest.mark.asyncio
    async def test_column_count_mismatch_raises(self) -> None:
        table = _users_table()
        bad = json.dumps(
            {
                "description": "d",
                "grain": "g",
                "domain_hints": [],
                "columns": [
                    {
                        "name": "user_id",
                        "description": "d",
                        "semantic_type": "identifier",
                        "pii_risk": "none",
                        "confidence": 0.5,
                    }
                ],
                "confidence": 0.5,
            }
        )
        client = FakeLLMClient(responses=[bad, bad])
        engine = DescriptionEngine(client)

        with pytest.raises(DescriptionParseError):
            await engine.describe_table(table, _users_samples())

    @pytest.mark.asyncio
    async def test_column_name_mismatch_raises(self) -> None:
        table = _users_table()
        swapped = json.dumps(
            {
                "description": "d",
                "grain": "g",
                "domain_hints": [],
                "columns": [
                    {
                        "name": "email",
                        "description": "d",
                        "semantic_type": "dimension",
                        "pii_risk": "low",
                        "confidence": 0.5,
                    },
                    {
                        "name": "user_id",
                        "description": "d",
                        "semantic_type": "identifier",
                        "pii_risk": "none",
                        "confidence": 0.5,
                    },
                    {
                        "name": "created_at",
                        "description": "d",
                        "semantic_type": "dimension",
                        "pii_risk": "none",
                        "confidence": 0.5,
                    },
                ],
                "confidence": 0.5,
            }
        )
        client = FakeLLMClient(responses=[swapped, swapped])
        engine = DescriptionEngine(client)

        with pytest.raises(DescriptionParseError):
            await engine.describe_table(table, _users_samples())

    @pytest.mark.asyncio
    async def test_valid_first_call_no_retry(self) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=[_valid_payload_for(table)])
        engine = DescriptionEngine(client)

        await engine.describe_table(table, _users_samples())

        assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# describe_database
# ---------------------------------------------------------------------------


def _tiny_table(i: int) -> Table:
    return Table(
        schema="public",
        name=f"t{i}",
        columns=(Column(name="id", data_type="int", nullable=False, is_primary_key=True),),
    )


class TestDescribeDatabase:
    @pytest.mark.asyncio
    async def test_concurrency_bound(self) -> None:
        tables = [_tiny_table(i) for i in range(10)]
        samples = {(t.schema, t.name): [] for t in tables}
        responses = [_valid_payload_for(t) for t in tables]
        client = FakeLLMClient(responses=responses, delay=0.02)
        engine = DescriptionEngine(client, config=LLMConfig(max_concurrent_calls=3))

        result = await engine.describe_database(tables, samples)

        assert len(result) == 10
        assert client.peak_concurrent <= 3
        assert client.peak_concurrent >= 1

    @pytest.mark.asyncio
    async def test_partial_failure_does_not_cancel(self) -> None:
        tables = [_tiny_table(i) for i in range(5)]
        samples = {(t.schema, t.name): [] for t in tables}

        def _respond(prompt: str, system: str | None) -> str:
            if "public.t2" in prompt:
                return "this is not json"
            for t in tables:
                if f"public.{t.name}" in prompt:
                    return _valid_payload_for(t)
            raise AssertionError("unknown table prompt")

        client = FakeLLMClient(response_for=_respond)
        engine = DescriptionEngine(client, config=LLMConfig(max_concurrent_calls=5))

        result = await engine.describe_database(tables, samples)

        assert len(result) == 5
        assert result[("public", "t2")] is None
        others = [v for k, v in result.items() if k != ("public", "t2")]
        assert len(others) == 4
        assert all(isinstance(v, TableDescription) for v in others)

    @pytest.mark.asyncio
    async def test_empty_input_short_circuits(self) -> None:
        client = FakeLLMClient(responses=[])
        engine = DescriptionEngine(client)

        result = await engine.describe_database([], {})

        assert result == {}
        assert client.calls == []


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    @pytest.mark.asyncio
    async def test_ok_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=[_valid_payload_for(table)])
        engine = DescriptionEngine(client)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.engine.describe"):
            await engine.describe_table(table, _users_samples())

        records = [r for r in caplog.records if r.name == "sonar.engine.describe"]
        assert len(records) == 1
        rec = records[0]
        assert rec.schema == "public"
        assert rec.table == "users"
        assert rec.columns_count == 3
        assert rec.outcome == "ok"

    @pytest.mark.asyncio
    async def test_parse_retry_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        table = _users_table()
        client = FakeLLMClient(responses=["not json", _valid_payload_for(table)])
        engine = DescriptionEngine(client)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.engine.describe"):
            await engine.describe_table(table, _users_samples())

        records = [r for r in caplog.records if r.name == "sonar.engine.describe"]
        assert len(records) == 1
        assert records[0].outcome == "parse_retry"

    @pytest.mark.asyncio
    async def test_failed_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        table = _users_table()
        sample_secret = "super-secret-sample"
        client = FakeLLMClient(responses=["nope", f"still nope {sample_secret}"])
        engine = DescriptionEngine(client)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.engine.describe"):
            with pytest.raises(DescriptionParseError):
                await engine.describe_table(table, [{"secret": sample_secret}])

        records = [r for r in caplog.records if r.name == "sonar.engine.describe"]
        assert len(records) == 1
        rec = records[0]
        assert rec.outcome == "failed"
        for value in rec.__dict__.values():
            if isinstance(value, str):
                assert sample_secret not in value


# ---------------------------------------------------------------------------
# Prompt builder direct test (coverage of _prompts.py)
# ---------------------------------------------------------------------------


def test_build_table_prompt_shape() -> None:
    table = _users_table()
    prompt = build_table_prompt(table, _users_samples())
    assert "public.users" in prompt
    assert "user_id: uuid, nullable=false, pk=true" in prompt
    assert "email: text, nullable=false, pk=false" in prompt
    assert "a@x.com" in prompt
