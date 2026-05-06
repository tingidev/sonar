"""Unit tests for search relevance evaluation."""

from __future__ import annotations

import pytest

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.eval.search import (
    GroundTruthError,
    GroundTruthQuery,
    evaluate_search,
    load_ground_truth,
    parse_ground_truth,
)
from sonar.index.bundle import BundleMeta, ContextBundle


def _bundle() -> ContextBundle:
    tables = (
        Table(
            schema="public",
            name="molecule_dictionary",
            columns=(Column("id", "int", False, True),),
        ),
        Table(
            schema="public",
            name="compound_structures",
            columns=(Column("id", "int", False, True),),
        ),
        Table(
            schema="public",
            name="target_dictionary",
            columns=(Column("id", "int", False, True),),
        ),
        Table(
            schema="public",
            name="assays",
            columns=(Column("id", "int", False, True),),
        ),
    )
    cols = (
        ColumnDescription(
            name="id",
            description="pk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
    )
    description_text = {
        "molecule_dictionary": "molecule dictionary",
        "compound_structures": "molecule chemical structures",
        "target_dictionary": "biological target",
        "assays": "experimental assay",
    }
    descriptions: dict[tuple[str, str], TableDescription | None] = {}
    for t in tables:
        descriptions[(t.schema, t.name)] = TableDescription(
            schema=t.schema,
            name=t.name,
            description=description_text[t.name],
            grain="row",
            domain_hints=(),
            columns=cols,
            confidence=0.9,
        )
    meta = BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="x",
    )
    return ContextBundle(meta=meta, tables=tables, descriptions=descriptions, relationships=())


class TestParseGroundTruth:
    def test_valid_input(self) -> None:
        raw = {
            "queries": [
                {"query": "molecule", "expected": ["public.molecule_dictionary"]},
            ]
        }
        parsed = parse_ground_truth(raw)
        assert len(parsed) == 1
        assert parsed[0].query == "molecule"
        assert parsed[0].expected == ("public.molecule_dictionary",)

    def test_missing_queries_key_raises(self) -> None:
        with pytest.raises(GroundTruthError):
            parse_ground_truth({"foo": "bar"})

    def test_empty_queries_list_raises(self) -> None:
        with pytest.raises(GroundTruthError):
            parse_ground_truth({"queries": []})

    def test_query_missing_expected_raises(self) -> None:
        with pytest.raises(GroundTruthError):
            parse_ground_truth({"queries": [{"query": "x"}]})

    def test_expected_must_be_list_of_strings(self) -> None:
        with pytest.raises(GroundTruthError):
            parse_ground_truth({"queries": [{"query": "x", "expected": [1, 2]}]})

    def test_load_ground_truth_invalid_yaml(self, tmp_path) -> None:
        path = tmp_path / "gt.yaml"
        path.write_text(":\n :\n: invalid yaml :::", encoding="utf-8")
        with pytest.raises(GroundTruthError):
            load_ground_truth(path)

    def test_load_ground_truth_round_trip(self, tmp_path) -> None:
        path = tmp_path / "gt.yaml"
        path.write_text(
            "queries:\n"
            "  - query: molecule\n"
            "    expected:\n"
            "      - public.molecule_dictionary\n",
            encoding="utf-8",
        )
        parsed = load_ground_truth(path)
        assert len(parsed) == 1
        assert parsed[0].query == "molecule"


class TestEvaluateSearch:
    def test_all_expected_found(self) -> None:
        gt = [
            GroundTruthQuery(
                query="molecule",
                expected=("public.molecule_dictionary", "public.compound_structures"),
            )
        ]
        report = evaluate_search(_bundle(), gt)
        per_q = report.per_query[0]
        assert per_q.recall_at_k == 1.0
        assert per_q.reciprocal_rank > 0.0

    def test_partial_match(self) -> None:
        gt = [
            GroundTruthQuery(
                query="molecule",
                expected=(
                    "public.molecule_dictionary",
                    "public.does_not_exist",
                ),
            )
        ]
        report = evaluate_search(_bundle(), gt)
        per_q = report.per_query[0]
        assert per_q.recall_at_k == 0.5

    def test_empty_results_for_query(self) -> None:
        gt = [
            GroundTruthQuery(
                query="zzznotamatch",
                expected=("public.molecule_dictionary",),
            )
        ]
        report = evaluate_search(_bundle(), gt)
        per_q = report.per_query[0]
        assert per_q.precision_at_k == 0.0
        assert per_q.recall_at_k == 0.0
        assert per_q.reciprocal_rank == 0.0

    def test_aggregate_mrr(self) -> None:
        gt = [
            GroundTruthQuery(
                query="molecule_dictionary",
                expected=("public.molecule_dictionary",),
            ),
            GroundTruthQuery(
                query="zzzz",
                expected=("public.molecule_dictionary",),
            ),
        ]
        report = evaluate_search(_bundle(), gt)
        assert report.query_count == 2
        # First query yields RR=1.0, second 0.0 -> MRR = 0.5
        assert abs(report.mean_reciprocal_rank - 0.5) < 1e-9
