"""API routes for metrics and analytics."""

from __future__ import annotations

from fastapi import APIRouter, Query

from agent_host.config import get_config
from metrics.aggregator import MetricsAggregator

router = APIRouter()


@router.get("/metrics", response_model=dict)
async def get_metrics(
    days: int = Query(7, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
) -> dict:
    """
    Get aggregated metrics for the requested time range.

    Query parameters:
    - days: Number of days to look back (default: 7, max: 365)
    - start_date: ISO format date string (YYYY-MM-DD) to override days
    - end_date: ISO format date string (YYYY-MM-DD)

    Returns:
    Aggregated metrics including query counts, latencies, costs, and breakdowns by intent/date.
    """
    config = get_config()
    aggregator = MetricsAggregator(config.artifact_path)

    metrics = aggregator.get_metrics(days=days, start_date=start_date, end_date=end_date)
    return metrics.to_dict()


@router.post("/metrics/refresh")
async def refresh_metrics() -> dict:
    """
    Force a refresh of metrics by rescanning all traces and audit logs.

    This is normally unnecessary (metrics are aggregated on-demand), but useful
    after bulk imports or if you suspect data consistency issues.

    Returns:
    Status confirmation with timestamp.
    """
    config = get_config()
    aggregator = MetricsAggregator(config.artifact_path)

    # Just verify the scan works
    try:
        metrics = aggregator.get_metrics(days=None)
        return {
            "status": "refreshed",
            "queries": metrics.total_queries,
            "timestamp": metrics.last_updated,
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": str(e),
        }


@router.get("/metrics/by-intent")
async def metrics_by_intent(days: int = Query(7, ge=1, le=365)) -> dict:
    """
    Get metrics broken down by detected intent.

    Returns:
    Dictionary mapping intent names to their individual metrics.
    """
    config = get_config()
    aggregator = MetricsAggregator(config.artifact_path)
    metrics = aggregator.get_metrics(days=days)

    return {
        "intents": {
            name: {
                "count": m.count,
                "avg_latency_ms": round(m.avg_latency_ms, 2),
                "success_rate": round(m.success_rate, 2),
                "rejection_rate": round(m.rejection_rate, 2),
                "avg_tokens": m.avg_tokens,
            }
            for name, m in metrics.by_intent.items()
        },
        "period": f"last {days} days",
    }


@router.get("/metrics/by-date")
async def metrics_by_date(days: int = Query(7, ge=1, le=365)) -> dict:
    """
    Get metrics broken down by date.

    Returns:
    Dictionary mapping dates to their daily metrics.
    """
    config = get_config()
    aggregator = MetricsAggregator(config.artifact_path)
    metrics = aggregator.get_metrics(days=days)

    return {
        "daily": {
            date: {
                "queries": m.queries,
                "total_tokens": m.total_tokens,
                "avg_latency_ms": round(m.avg_latency_ms, 2),
                "rejections": m.rejections,
                "clarifications": m.clarifications,
                "estimated_cost": round(m.estimated_cost, 2),
                "success_rate": round(m.success_rate, 2),
            }
            for date, m in metrics.by_date.items()
        },
        "period": f"last {days} days",
    }


@router.get("/metrics/summary")
async def metrics_summary(days: int = Query(7, ge=1, le=365)) -> dict:
    """
    Get high-level summary metrics.

    Returns:
    Key metrics suitable for executive dashboards.
    """
    config = get_config()
    aggregator = MetricsAggregator(config.artifact_path)
    metrics = aggregator.get_metrics(days=days)

    return {
        "summary": {
            "total_queries": metrics.total_queries,
            "avg_latency_ms": round(metrics.avg_latency_ms, 2),
            "policy_rejection_rate": round(metrics.policy_rejection_rate, 2),
            "sql_success_rate": round(metrics.sql_generation_success_rate, 2),
            "estimated_cost": round(metrics.estimated_bigquery_cost, 2),
        },
        "period": f"last {days} days",
        "period_start": metrics.earliest_query,
        "period_end": metrics.latest_query,
    }
