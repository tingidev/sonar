"""Direct tests for the report formatters in `sonar.eval._report`."""

from __future__ import annotations

import json

from sonar.eval._report import (
    format_descriptions_human,
    format_descriptions_json,
    format_diff_human,
    format_diff_json,
    format_quality_human,
    format_quality_json,
    format_relationships_human,
    format_relationships_json,
    format_search_human,
    format_search_json,
)
from sonar.eval._types import RelationshipEdge
from sonar.eval.descriptions import DescriptionQualityReport, TableScore
from sonar.eval.diff import DescriptionChange, DiffReport
from sonar.eval.quality import (
    ConfidenceSummary,
    GraphSummary,
    QualityReport,
)
from sonar.eval.relationships import (
    RelationshipReport,
    TableBreakdown,
)
from sonar.eval.search import QueryResult, SearchReport


def _quality_report() -> QualityReport:
    return QualityReport(
        bundle_dir=".sonar/",
        table_count=10,
        description_present=8,
        description_null=2,
        description_coverage=0.8,
        relationship_count=12,
        relationship_coverage=0.7,
        orphan_tables=(("public", "lonely1"), ("public", "lonely2"), ("public", "lonely3")),
        graph=GraphSummary(
            components=2,
            largest_component_size=7,
            largest_component_fraction=0.7,
            mean_reachable=5.4,
        ),
        pii_distribution={"none": 30, "low": 5, "medium": 2, "high": 1},
        table_confidence=ConfidenceSummary(mean=0.85, minimum=0.6, maximum=1.0, count=8),
        column_confidence=ConfidenceSummary(mean=0.78, minimum=0.5, maximum=0.95, count=40),
    )


class TestQualityFormatters:
    def test_human_contains_key_lines(self) -> None:
        text = format_quality_human(_quality_report())
        assert "Tables: 10" in text
        assert "Description coverage" in text
        assert "Components: 2" in text
        assert "PII distribution" in text
        assert "high: 1" in text
        assert "Table confidence" in text

    def test_human_truncates_orphan_lists(self) -> None:
        report = QualityReport(
            bundle_dir=".sonar/",
            table_count=15,
            description_present=15,
            description_null=0,
            description_coverage=1.0,
            relationship_count=0,
            relationship_coverage=0.0,
            orphan_tables=tuple(("public", f"o{i}") for i in range(15)),
            graph=GraphSummary(15, 1, 1 / 15, 1.0),
            pii_distribution={r: 0 for r in ("none", "low", "medium", "high")},
            table_confidence=None,
            column_confidence=None,
        )
        text = format_quality_human(report)
        assert "+5 more" in text

    def test_json_envelope(self) -> None:
        out = format_quality_json(_quality_report())
        payload = json.loads(out)
        assert payload["mode"] == "quality"
        assert payload["metrics"]["table_count"] == 10
        assert payload["metrics"]["pii_distribution"]["high"] == 1
        assert payload["details"]["orphan_tables"][0]["name"] == "lonely1"


class TestRelationshipsFormatters:
    def _report(self) -> RelationshipReport:
        return RelationshipReport(
            declared_count=10,
            inferred_count=8,
            matched_count=7,
            recall=0.7,
            precision=0.875,
            f1=0.7777,
            missed=(
                RelationshipEdge("public", "a", "x", "public", "b", "id"),
                RelationshipEdge("public", "c", "y", "public", "d", "id"),
            ),
            false_positive=(
                RelationshipEdge("public", "e", "z", "public", "f", "id"),
            ),
            per_table=(
                TableBreakdown("public", "a", 1, 0, 1, 0),
                TableBreakdown("public", "e", 0, 0, 0, 1),
            ),
        )

    def test_human_lists_metrics_and_details(self) -> None:
        text = format_relationships_human(self._report(), "test/db")
        assert "Database: test/db" in text
        assert "Recall: 0.700" in text
        assert "Missed declared FKs (2)" in text
        assert "False-positive inferences (1)" in text
        assert "Per-table breakdown" in text

    def test_human_handles_undefined_metrics(self) -> None:
        report = RelationshipReport(
            declared_count=0,
            inferred_count=0,
            matched_count=0,
            recall=None,
            precision=None,
            f1=None,
            missed=(),
            false_positive=(),
            per_table=(),
        )
        text = format_relationships_human(report, "x")
        assert "Recall: n/a" in text

    def test_json_payload(self) -> None:
        out = format_relationships_json(self._report(), "test/db")
        payload = json.loads(out)
        assert payload["mode"] == "relationships"
        assert payload["metrics"]["recall"] == 0.7
        assert len(payload["details"]["missed"]) == 2
        assert payload["details"]["per_table"][0]["schema"] == "public"


class TestSearchFormatters:
    def _report(self) -> SearchReport:
        return SearchReport(
            query_count=2,
            mean_reciprocal_rank=0.75,
            mean_precision_at_k=0.6,
            mean_recall_at_k=0.55,
            per_query=(
                QueryResult(
                    query="molecule",
                    expected=("public.molecule_dictionary",),
                    returned=("public.molecule_dictionary", "public.compound_structures"),
                    precision_at_k=0.5,
                    recall_at_k=1.0,
                    reciprocal_rank=1.0,
                ),
                QueryResult(
                    query="zzz",
                    expected=("public.x",),
                    returned=(),
                    precision_at_k=0.0,
                    recall_at_k=0.0,
                    reciprocal_rank=0.0,
                ),
            ),
        )

    def test_human_lists_per_query(self) -> None:
        text = format_search_human(self._report(), ".sonar/")
        assert "Queries: 2" in text
        assert "MRR: 0.750" in text
        assert "'molecule'" in text
        assert "'zzz'" in text

    def test_json_payload(self) -> None:
        out = format_search_json(self._report(), ".sonar/")
        payload = json.loads(out)
        assert payload["mode"] == "search"
        assert payload["metrics"]["query_count"] == 2
        assert len(payload["details"]) == 2


class TestDiffFormatters:
    def _report(self) -> DiffReport:
        return DiffReport(
            tables_added=(("public", "new1"),),
            tables_removed=(("public", "old1"),),
            relationships_added=(
                RelationshipEdge(
                    "public", "a", "x", "public", "b", "id", "inferred"
                ),
            ),
            relationships_removed=(
                RelationshipEdge(
                    "public", "c", "y", "public", "d", "id", "declared"
                ),
            ),
            descriptions_added=(("public", "new1"),),
            descriptions_removed=(("public", "old1"),),
            descriptions_changed=(
                DescriptionChange(
                    schema="public",
                    name="orders",
                    text_changed=True,
                    confidence_delta=0.05,
                    grain_changed=False,
                    domain_hints_added=("commerce",),
                    domain_hints_removed=(),
                ),
                DescriptionChange(
                    schema="public",
                    name="users",
                    text_changed=False,
                    confidence_delta=0.0,
                    grain_changed=True,
                    domain_hints_added=(),
                    domain_hints_removed=(),
                ),
            ),
        )

    def test_human_lists_changes(self) -> None:
        text = format_diff_human(self._report(), ".sonar/", "/tmp/old/")
        assert "Tables added: 1" in text
        assert "+ public.new1" in text
        assert "Tables removed: 1" in text
        assert "- public.old1" in text
        assert "Relationships added: 1" in text
        assert "[inferred]" in text
        assert "[declared]" in text
        assert "Descriptions changed: 2" in text
        assert "text" in text

    def test_json_payload(self) -> None:
        out = format_diff_json(self._report(), ".sonar/", "/tmp/old/")
        payload = json.loads(out)
        assert payload["mode"] == "diff"
        assert payload["metrics"]["tables_added"] == 1
        assert payload["metrics"]["descriptions_changed"] == 2
        assert payload["details"]["other"] == "/tmp/old/"


class TestDescriptionsFormatters:
    def _report(self) -> DescriptionQualityReport:
        return DescriptionQualityReport(
            scored_count=2,
            skipped_null=1,
            judge_failures=0,
            mean_accuracy=0.85,
            mean_completeness=0.78,
            mean_specificity=0.72,
            flagged=(TableScore("public", "weak", 0.4, 0.7, 0.5),),
            per_table=(
                TableScore("public", "strong", 0.95, 0.9, 0.92),
                TableScore("public", "weak", 0.4, 0.7, 0.5),
            ),
        )

    def test_human_includes_means_and_flagged(self) -> None:
        text = format_descriptions_human(self._report(), ".sonar/")
        assert "Scored: 2" in text
        assert "Mean accuracy: 0.85" in text
        assert "Flagged tables (1" in text
        assert "public.weak" in text

    def test_human_no_flagged_when_empty(self) -> None:
        report = DescriptionQualityReport(
            scored_count=0,
            skipped_null=0,
            judge_failures=2,
            mean_accuracy=0.0,
            mean_completeness=0.0,
            mean_specificity=0.0,
            flagged=(),
            per_table=(),
        )
        text = format_descriptions_human(report, ".sonar/")
        assert "Flagged" not in text
        assert "Mean accuracy" not in text

    def test_json_payload(self) -> None:
        out = format_descriptions_json(self._report(), ".sonar/")
        payload = json.loads(out)
        assert payload["mode"] == "descriptions"
        assert payload["metrics"]["scored_count"] == 2
        assert len(payload["details"]["per_table"]) == 2
        assert len(payload["details"]["flagged"]) == 1
