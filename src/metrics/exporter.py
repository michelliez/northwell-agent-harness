"""Export metrics in various formats (JSON, CSV)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .models import MetricsSummary


def export_json(metrics: MetricsSummary) -> str:
    """Export metrics as formatted JSON."""
    return json.dumps(metrics.to_dict(), indent=2)


def export_csv(metrics: MetricsSummary) -> str:
    """Export metrics as CSV format (summary + daily breakdown)."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Summary section
    writer.writerow(["Metrics Summary"])
    writer.writerow(["Metric", "Value"])
    summary_data = metrics.to_dict()
    for key, value in summary_data.items():
        if key not in ("by_intent", "by_date", "last_updated"):
            writer.writerow([key, value])

    writer.writerow([])
    writer.writerow(["Daily Breakdown"])
    writer.writerow(
        [
            "Date",
            "Queries",
            "Avg Latency (ms)",
            "Total Tokens",
            "Rejections",
            "Clarifications",
            "Estimated Cost",
            "Success Rate (%)",
        ]
    )

    for date in sorted(metrics.by_date.keys()):
        daily = metrics.by_date[date]
        writer.writerow(
            [
                daily.date,
                daily.queries,
                f"{daily.avg_latency_ms:.2f}",
                daily.total_tokens,
                daily.rejections,
                daily.clarifications,
                f"${daily.estimated_cost:.2f}",
                f"{daily.success_rate:.1f}",
            ]
        )

    writer.writerow([])
    writer.writerow(["By Intent"])
    writer.writerow(
        [
            "Intent",
            "Count",
            "Avg Latency (ms)",
            "Success Rate (%)",
            "Rejection Rate (%)",
            "Avg Tokens",
        ]
    )

    for intent in sorted(metrics.by_intent.keys()):
        intent_metric = metrics.by_intent[intent]
        writer.writerow(
            [
                intent_metric.intent,
                intent_metric.count,
                f"{intent_metric.avg_latency_ms:.2f}",
                f"{intent_metric.success_rate:.1f}",
                f"{intent_metric.rejection_rate:.1f}",
                intent_metric.avg_tokens,
            ]
        )

    return output.getvalue()


def export_markdown(metrics: MetricsSummary) -> str:
    """Export metrics as Markdown report (suitable for documentation/email)."""
    lines = [
        "# Clarity Agent Metrics Report",
        "",
        f"**Generated:** {datetime.utcnow().isoformat()}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Queries | {metrics.total_queries} |",
        f"| Total Tokens | {metrics.total_tokens:,} |",
        f"| Avg Latency | {metrics.avg_latency_ms:.0f}ms |",
        f"| Policy Rejection Rate | {metrics.policy_rejection_rate:.1f}% |",
        f"| SQL Success Rate | {metrics.sql_generation_success_rate:.1f}% |",
        f"| Est. BigQuery Cost | ${metrics.estimated_bigquery_cost:.2f} |",
        f"| Avg Cost per Query | ${metrics.avg_cost_per_query:.4f} |",
        "",
        "## By Intent",
        "",
    ]

    if metrics.by_intent:
        lines.extend(
            [
                "| Intent | Count | Avg Latency | Success Rate | Tokens |",
                "|--------|-------|-------------|--------------|--------|",
            ]
        )
        for _intent, metric in sorted(metrics.by_intent.items()):
            lines.append(
                f"| {metric.intent} | {metric.count} | {metric.avg_latency_ms:.0f}ms | "
                f"{metric.success_rate:.1f}% | {metric.avg_tokens} |"
            )
    else:
        lines.append("No data available")

    lines.extend(
        [
            "",
            "## Daily Breakdown",
            "",
        ]
    )

    if metrics.by_date:
        lines.extend(
            [
                "| Date | Queries | Avg Latency | Cost | Success Rate |",
                "|------|---------|-------------|------|--------------|",
            ]
        )
        for date in sorted(metrics.by_date.keys()):
            daily = metrics.by_date[date]
            lines.append(
                f"| {daily.date} | {daily.queries} | {daily.avg_latency_ms:.0f}ms | "
                f"${daily.estimated_cost:.2f} | {daily.success_rate:.1f}% |"
            )
    else:
        lines.append("No data available")

    return "\n".join(lines)
