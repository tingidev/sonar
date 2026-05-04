"""Bundle quality report — coverage, reachability, confidence, PII distribution."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from sonar.engine.describe import PIIRisk
from sonar.index.bundle import ContextBundle


@dataclass(frozen=True)
class ConfidenceSummary:
    mean: float
    minimum: float
    maximum: float
    count: int


@dataclass(frozen=True)
class GraphSummary:
    components: int
    largest_component_size: int
    largest_component_fraction: float
    mean_reachable: float


@dataclass(frozen=True)
class QualityReport:
    bundle_dir: str
    table_count: int
    description_present: int
    description_null: int
    description_coverage: float
    relationship_count: int
    relationship_coverage: float
    orphan_tables: tuple[tuple[str, str], ...]
    graph: GraphSummary
    pii_distribution: dict[str, int] = field(default_factory=dict)
    table_confidence: ConfidenceSummary | None = None
    column_confidence: ConfidenceSummary | None = None


def build_quality_report(bundle: ContextBundle, bundle_dir: str) -> QualityReport:
    table_keys = [(t.schema, t.name) for t in bundle.tables]
    table_count = len(table_keys)

    description_present = sum(1 for v in bundle.descriptions.values() if v is not None)
    description_null = sum(1 for v in bundle.descriptions.values() if v is None)
    description_coverage = (
        description_present / table_count if table_count > 0 else 0.0
    )

    incident: dict[tuple[str, str], int] = defaultdict(int)
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for r in bundle.relationships:
        src = (r.source_schema, r.source_table)
        tgt = (r.target_schema, r.target_table)
        incident[src] += 1
        incident[tgt] += 1
        adjacency[src].add(tgt)
        adjacency[tgt].add(src)

    tables_with_relationships = sum(1 for k in table_keys if incident.get(k, 0) > 0)
    relationship_coverage = (
        tables_with_relationships / table_count if table_count > 0 else 0.0
    )
    orphan_tables = tuple(k for k in table_keys if incident.get(k, 0) == 0)

    graph = _compute_graph_summary(table_keys, adjacency)

    pii_distribution = _compute_pii_distribution(bundle)
    table_conf, column_conf = _compute_confidence_summaries(bundle)

    return QualityReport(
        bundle_dir=bundle_dir,
        table_count=table_count,
        description_present=description_present,
        description_null=description_null,
        description_coverage=description_coverage,
        relationship_count=len(bundle.relationships),
        relationship_coverage=relationship_coverage,
        orphan_tables=orphan_tables,
        graph=graph,
        pii_distribution=pii_distribution,
        table_confidence=table_conf,
        column_confidence=column_conf,
    )


def _compute_graph_summary(
    table_keys: list[tuple[str, str]],
    adjacency: dict[tuple[str, str], set[tuple[str, str]]],
) -> GraphSummary:
    if not table_keys:
        return GraphSummary(
            components=0,
            largest_component_size=0,
            largest_component_fraction=0.0,
            mean_reachable=0.0,
        )

    visited: set[tuple[str, str]] = set()
    component_sizes: list[int] = []
    component_id: dict[tuple[str, str], int] = {}

    for start in table_keys:
        if start in visited:
            continue
        cid = len(component_sizes)
        size = 0
        queue: deque[tuple[str, str]] = deque([start])
        visited.add(start)
        while queue:
            node = queue.popleft()
            component_id[node] = cid
            size += 1
            for nb in adjacency.get(node, ()):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        component_sizes.append(size)

    largest = max(component_sizes)
    fraction = largest / len(table_keys)

    # Mean reachable tables per starting node = mean component size weighted by
    # node membership. For a node in a component of size c, c-1 other tables
    # are reachable, so reachable count = c. We report tables reachable
    # including self for symmetry with component size.
    total_reachable = sum(component_sizes[component_id[k]] for k in table_keys)
    mean_reachable = total_reachable / len(table_keys)

    return GraphSummary(
        components=len(component_sizes),
        largest_component_size=largest,
        largest_component_fraction=fraction,
        mean_reachable=mean_reachable,
    )


def _compute_pii_distribution(bundle: ContextBundle) -> dict[str, int]:
    distribution: dict[str, int] = {risk.value: 0 for risk in PIIRisk}
    for description in bundle.descriptions.values():
        if description is None:
            continue
        for col in description.columns:
            distribution[col.pii_risk.value] += 1
    return distribution


def _compute_confidence_summaries(
    bundle: ContextBundle,
) -> tuple[ConfidenceSummary | None, ConfidenceSummary | None]:
    table_scores: list[float] = []
    column_scores: list[float] = []
    for description in bundle.descriptions.values():
        if description is None:
            continue
        table_scores.append(description.confidence)
        for col in description.columns:
            column_scores.append(col.confidence)
    return _summarise(table_scores), _summarise(column_scores)


def _summarise(values: list[float]) -> ConfidenceSummary | None:
    if not values:
        return None
    return ConfidenceSummary(
        mean=sum(values) / len(values),
        minimum=min(values),
        maximum=max(values),
        count=len(values),
    )
