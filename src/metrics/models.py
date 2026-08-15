"""Data models for metrics aggregation and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class IntentMetrics:
    """Metrics grouped by detected intent."""

    intent: str
    count: int
    avg_latency_ms: float
    success_rate: float
    rejection_rate: float
    avg_tokens: int


@dataclass
class DailyMetrics:
    """Metrics for a single day."""

    date: str
    queries: int
    total_tokens: int
    total_latency_ms: int
    avg_latency_ms: float
    rejections: int
    clarifications: int
    estimated_cost: float
    success_rate: float


@dataclass
class MetricsSummary:
    """Aggregated metrics across all queries."""

    # Overall statistics
    total_queries: int
    total_tokens: int
    avg_latency_ms: float
    total_latency_ms: int
    earliest_query: str | None
    latest_query: str | None

    # Policy and quality metrics
    policy_rejection_rate: float
    clarification_rate: float
    sql_generation_success_rate: float
    avg_retrieval_coverage: float

    # Cost metrics
    estimated_bigquery_cost: float
    avg_cost_per_query: float

    # Breakdowns
    by_intent: dict[str, IntentMetrics] = field(default_factory=dict)
    by_date: dict[str, DailyMetrics] = field(default_factory=dict)

    # Metadata
    sample_count: int = 0
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "total_queries": self.total_queries,
            "total_tokens": self.total_tokens,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_latency_ms": self.total_latency_ms,
            "earliest_query": self.earliest_query,
            "latest_query": self.latest_query,
            "policy_rejection_rate": round(self.policy_rejection_rate, 2),
            "clarification_rate": round(self.clarification_rate, 2),
            "sql_generation_success_rate": round(self.sql_generation_success_rate, 2),
            "avg_retrieval_coverage": round(self.avg_retrieval_coverage, 2),
            "estimated_bigquery_cost": round(self.estimated_bigquery_cost, 2),
            "avg_cost_per_query": round(self.avg_cost_per_query, 4),
            "by_intent": {
                name: {
                    "intent": m.intent,
                    "count": m.count,
                    "avg_latency_ms": round(m.avg_latency_ms, 2),
                    "success_rate": round(m.success_rate, 2),
                    "rejection_rate": round(m.rejection_rate, 2),
                    "avg_tokens": m.avg_tokens,
                }
                for name, m in self.by_intent.items()
            },
            "by_date": {
                date: {
                    "date": m.date,
                    "queries": m.queries,
                    "total_tokens": m.total_tokens,
                    "total_latency_ms": m.total_latency_ms,
                    "avg_latency_ms": round(m.avg_latency_ms, 2),
                    "rejections": m.rejections,
                    "clarifications": m.clarifications,
                    "estimated_cost": round(m.estimated_cost, 2),
                    "success_rate": round(m.success_rate, 2),
                }
                for date, m in self.by_date.items()
            },
            "sample_count": self.sample_count,
            "last_updated": self.last_updated,
        }
