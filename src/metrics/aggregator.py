"""Aggregate traces and audit logs into metrics summaries."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .models import DailyMetrics, IntentMetrics, MetricsSummary, NodeMetrics


class MetricsAggregator:
    """Aggregates execution traces and audit logs into metrics."""

    def __init__(self, artifact_path: Path | str = ".local"):
        self.artifact_path = Path(artifact_path)
        self.trace_dir = self.artifact_path / "traces"
        self.audit_dir = self.artifact_path / "audit_logs"

    def get_metrics(
        self,
        days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> MetricsSummary:
        """
        Aggregate metrics for the specified date range.

        Args:
            days: Number of days back (default: all available)
            start_date: ISO format date string (YYYY-MM-DD)
            end_date: ISO format date string (YYYY-MM-DD)

        Returns:
            MetricsSummary with aggregated metrics
        """
        # Load audit logs and traces
        runs = self._load_audit_logs(days, start_date, end_date)
        traces = self._load_traces(days, start_date, end_date)

        # If no audit logs, use traces as source of runs
        if not runs and traces:
            runs = {run_id: [] for run_id in traces.keys()}

        if not runs:
            return MetricsSummary(
                total_queries=0,
                total_tokens=0,
                avg_latency_ms=0,
                total_latency_ms=0,
                earliest_query=None,
                latest_query=None,
                policy_rejection_rate=0,
                clarification_rate=0,
                sql_generation_success_rate=0,
                avg_retrieval_coverage=0,
                estimated_bigquery_cost=0,
                avg_cost_per_query=0,
            )

        return self._compute_summary(runs, traces)

    def _load_audit_logs(
        self,
        days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[dict]]:
        """Load audit logs and filter by date range."""
        runs = defaultdict(list)

        if not self.audit_dir.exists():
            return runs

        cutoff = self._get_cutoff_date(days, start_date, end_date)

        for audit_file in self.audit_dir.glob("*.jsonl"):
            with open(audit_file) as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        if not event:
                            continue

                        run_id = event.get("run_id")
                        timestamp = event.get("timestamp", "")

                        if cutoff and timestamp < cutoff:
                            continue

                        runs[run_id].append(event)
                    except (json.JSONDecodeError, KeyError):
                        continue

        return runs

    def _load_traces(
        self,
        days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[dict]]:
        """Load trace files and extract timing information."""
        traces = defaultdict(list)

        if not self.trace_dir.exists():
            return traces

        cutoff = self._get_cutoff_date(days, start_date, end_date)

        for trace_file in self.trace_dir.glob("*.jsonl"):
            run_id = trace_file.stem
            with open(trace_file) as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        if not event:
                            continue

                        ts = event.get("ts")
                        if cutoff and ts:
                            ts_dt = datetime.fromtimestamp(ts)
                            if ts_dt.isoformat() < cutoff:
                                continue

                        traces[run_id].append(event)
                    except (json.JSONDecodeError, KeyError):
                        continue

        return traces

    def _compute_summary(self, runs: dict, traces: dict) -> MetricsSummary:
        """Compute aggregated metrics from audit logs and traces."""
        total_queries = len(runs)
        total_tokens = 0
        total_latency_ms = 0
        rejection_count = 0
        clarification_count = 0
        sql_success_count = 0
        sql_attempt_count = 0
        total_cost = 0
        retrieval_coverage_sum = 0

        by_intent: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "latency_sum": 0,
                "success_count": 0,
                "rejection_count": 0,
                "tokens_sum": 0,
            }
        )

        by_date: dict[str, dict] = defaultdict(
            lambda: {
                "queries": 0,
                "tokens": 0,
                "latency_sum": 0,
                "rejections": 0,
                "clarifications": 0,
                "cost": 0,
                "success_count": 0,
                "attempt_count": 0,
            }
        )

        earliest = None
        latest = None

        for run_id, audit_events in runs.items():
            trace_events = traces.get(run_id, [])

            # Extract metrics from audit events
            run_tokens = self._extract_tokens(audit_events)
            run_latency = self._extract_latency(trace_events)
            run_intent = self._extract_intent(audit_events)
            run_date = self._extract_date(audit_events)
            run_cost = self._extract_cost(audit_events)
            run_rejected = self._is_rejected(audit_events)
            run_clarified = self._is_clarified(audit_events)
            run_sql_success = self._is_sql_success(trace_events)
            run_retrieval_coverage = self._extract_retrieval_coverage(audit_events)

            # Update totals
            total_tokens += run_tokens
            total_latency_ms += run_latency
            total_cost += run_cost

            if run_rejected:
                rejection_count += 1
            if run_clarified:
                clarification_count += 1
            if run_sql_success is not None:
                sql_attempt_count += 1
                if run_sql_success:
                    sql_success_count += 1

            if run_retrieval_coverage >= 0:
                retrieval_coverage_sum += run_retrieval_coverage

            # Track timestamps
            ts_str = self._extract_timestamp(audit_events)
            if ts_str:
                if not earliest or ts_str < earliest:
                    earliest = ts_str
                if not latest or ts_str > latest:
                    latest = ts_str

            # Update by_intent
            if run_intent:
                by_intent[run_intent]["count"] += 1
                by_intent[run_intent]["latency_sum"] += run_latency
                by_intent[run_intent]["tokens_sum"] += run_tokens
                if run_rejected:
                    by_intent[run_intent]["rejection_count"] += 1
                if run_sql_success:
                    by_intent[run_intent]["success_count"] += 1

            # Update by_date
            if run_date:
                by_date[run_date]["queries"] += 1
                by_date[run_date]["tokens"] += run_tokens
                by_date[run_date]["latency_sum"] += run_latency
                by_date[run_date]["cost"] += run_cost
                if run_rejected:
                    by_date[run_date]["rejections"] += 1
                if run_clarified:
                    by_date[run_date]["clarifications"] += 1
                if run_sql_success is not None:
                    by_date[run_date]["attempt_count"] += 1
                    if run_sql_success:
                        by_date[run_date]["success_count"] += 1

        # Compute averages and rates
        avg_latency = total_latency_ms / total_queries if total_queries > 0 else 0
        rejection_rate = (rejection_count / total_queries * 100) if total_queries > 0 else 0
        clarification_rate = (
            (clarification_count / total_queries * 100) if total_queries > 0 else 0
        )
        sql_success_rate = (
            (sql_success_count / sql_attempt_count * 100) if sql_attempt_count > 0 else 0
        )
        avg_retrieval = (
            (retrieval_coverage_sum / total_queries)
            if total_queries > 0
            else 0
        )
        avg_cost = total_cost / total_queries if total_queries > 0 else 0

        # Build by_intent summary
        intent_summary = {}
        for intent, data in by_intent.items():
            count = data["count"]
            intent_summary[intent] = IntentMetrics(
                intent=intent,
                count=count,
                avg_latency_ms=data["latency_sum"] / count if count > 0 else 0,
                success_rate=(
                    data["success_count"] / count * 100 if count > 0 else 0
                ),
                rejection_rate=(
                    data["rejection_count"] / count * 100 if count > 0 else 0
                ),
                avg_tokens=int(data["tokens_sum"] / count) if count > 0 else 0,
            )

        # Build by_date summary
        date_summary = {}
        for date, data in by_date.items():
            date_summary[date] = DailyMetrics(
                date=date,
                queries=data["queries"],
                total_tokens=data["tokens"],
                total_latency_ms=int(data["latency_sum"]),
                avg_latency_ms=data["latency_sum"] / data["queries"]
                if data["queries"] > 0
                else 0,
                rejections=data["rejections"],
                clarifications=data["clarifications"],
                estimated_cost=data["cost"],
                success_rate=(
                    data["success_count"] / data["attempt_count"] * 100
                    if data["attempt_count"] > 0
                    else 0
                ),
            )

        # Compute node-level metrics from traces and audit events
        node_summary = self._compute_node_metrics(traces, runs)

        return MetricsSummary(
            total_queries=total_queries,
            total_tokens=total_tokens,
            avg_latency_ms=avg_latency,
            total_latency_ms=int(total_latency_ms),
            earliest_query=earliest,
            latest_query=latest,
            policy_rejection_rate=rejection_rate,
            clarification_rate=clarification_rate,
            sql_generation_success_rate=sql_success_rate,
            avg_retrieval_coverage=avg_retrieval,
            estimated_bigquery_cost=total_cost,
            avg_cost_per_query=avg_cost,
            by_intent=intent_summary,
            by_date=date_summary,
            by_node=node_summary,
            sample_count=total_queries,
        )

    def _compute_node_metrics(
        self, traces: dict[str, list[dict]], audit_events: dict[str, list[dict]]
    ) -> dict[str, NodeMetrics]:
        """Compute per-node metrics from trace events and map tokens from audit events."""
        node_data: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "latencies": [],
                "success_count": 0,
                "failure_count": 0,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            }
        )

        # Map operation names to node names for token attribution
        operation_to_node = {
            "intent_classification": "Intent Classification",
            "schema_exploration": "Retrieval",
            "documentation_answer": "Answer Generation",
            "query_planning": "SQL Planning",
            "output_safety": "Result Safety",
            "policy_check": "Policy Gate",
        }

        # Map trace event names to node names
        event_to_node = {
            "policy_gate": "Policy Gate",
            "intent_classified": "Intent Classification",
            "retrieval": "Retrieval",
            "context_gate": "Context Gate",
            "sql_planning": "SQL Planning",
            "sql_compiled": "SQL Validation",
            "answer_generated": "Answer Generation",
            "result_safety": "Result Safety",
        }

        for run_id, events in traces.items():
            # Group events by node
            current_node = None
            node_start_time = None

            for event in events:
                event_name = event.get("event", "")
                ts = event.get("ts", 0)

                # Determine node from event name
                node = None
                for key, label in event_to_node.items():
                    if key in event_name:
                        node = label
                        break

                if node:
                    # Check if this is a new node (different from current)
                    if node != current_node and node_start_time is not None:
                        # Record latency for previous node
                        latency = (ts - node_start_time) * 1000  # Convert to ms
                        if latency > 0:
                            node_data[current_node]["latencies"].append(latency)
                            node_data[current_node]["count"] += 1

                    current_node = node
                    node_start_time = ts

                    # Check success/failure from event
                    result = event.get("result", {})
                    if isinstance(result, dict) and result.get("decision") == "rejected":
                        node_data[node]["failure_count"] += 1
                    elif "allowed" in event_name or "completed" in event_name:
                        node_data[node]["success_count"] += 1

        # Process audit events to extract token data
        for run_id, events in audit_events.items():
            for event in events:
                if event.get("event_type") == "model_usage":
                    operation = event.get("operation", "")
                    node_name = operation_to_node.get(operation)

                    if node_name:
                        node_data[node_name]["total_tokens"] += event.get(
                            "total_tokens", 0
                        )
                        node_data[node_name]["input_tokens"] += event.get(
                            "input_tokens", 0
                        )
                        node_data[node_name]["output_tokens"] += event.get(
                            "output_tokens", 0
                        )
                        node_data[node_name]["cache_creation_tokens"] += event.get(
                            "cache_creation_input_tokens", 0
                        )
                        node_data[node_name]["cache_read_tokens"] += event.get(
                            "cache_read_input_tokens", 0
                        )

        # Build node metrics
        node_summary = {}
        for node_name, data in node_data.items():
            latencies = data["latencies"]
            count = data["count"]
            success = data["success_count"]
            failure = data["failure_count"]
            total = success + failure if success + failure > 0 else count

            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            min_latency = min(latencies) if latencies else 0
            max_latency = max(latencies) if latencies else 0
            success_rate = (success / total * 100) if total > 0 else 0

            node_summary[node_name] = NodeMetrics(
                node_name=node_name,
                count=count,
                avg_latency_ms=avg_latency,
                min_latency_ms=min_latency,
                max_latency_ms=max_latency,
                success_count=success,
                failure_count=failure,
                success_rate=success_rate,
                total_tokens=data["total_tokens"],
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
                cache_creation_tokens=data["cache_creation_tokens"],
                cache_read_tokens=data["cache_read_tokens"],
            )

        return node_summary

    def _get_cutoff_date(
        self, days: int | None, start_date: str | None, end_date: str | None
    ) -> str | None:
        """Get ISO format cutoff date for filtering."""
        if start_date:
            return start_date
        if days:
            cutoff = datetime.utcnow() - timedelta(days=days)
            return cutoff.isoformat()
        return None

    def _extract_tokens(self, audit_events: list[dict]) -> int:
        """Extract token count from audit events."""
        return sum(
            int(event.get("total_tokens") or 0)
            for event in audit_events
            if event.get("event_type") == "model_usage"
        )

    def _extract_latency(self, trace_events: list[dict]) -> float:
        """Extract latency in milliseconds from trace events."""
        if not trace_events:
            return 0

        timestamps: list[float] = [
            float(timestamp)
            for event in trace_events
            if (timestamp := event.get("ts")) is not None
        ]
        if len(timestamps) < 2:
            return 0

        start_ts = min(timestamps)
        end_ts = max(timestamps)
        latency_seconds = end_ts - start_ts
        return latency_seconds * 1000

    def _extract_intent(self, audit_events: list[dict]) -> str | None:
        """Extract intent classification from audit events."""
        for event in audit_events:
            if event.get("event_type") == "intent_classified":
                # Could extract intent from reason or other field if available
                return "general"
        return None

    def _extract_date(self, audit_events: list[dict]) -> str | None:
        """Extract date in YYYY-MM-DD format from audit events."""
        if not audit_events:
            return None
        timestamp = audit_events[0].get("timestamp", "")
        if timestamp:
            return timestamp[:10]  # YYYY-MM-DD part of ISO string
        return None

    def _extract_cost(self, audit_events: list[dict]) -> float:
        """Extract total cost: BigQuery + Claude API token costs."""
        bq_cost = 0
        claude_cost = 0

        for event in audit_events:
            # BigQuery dry-run cost
            if event.get("event_type") == "cost_gate":
                bytes_processed = event.get("bytes_processed", 0)
                if bytes_processed:
                    bq_cost += (bytes_processed / (1024 ** 4)) * 6.25  # TB to cost

            # Claude API token cost
            if event.get("event_type") == "model_usage":
                input_tokens = event.get("input_tokens", 0)
                output_tokens = event.get("output_tokens", 0)
                cache_creation_tokens = event.get("cache_creation_input_tokens", 0)
                cache_read_tokens = event.get("cache_read_input_tokens", 0)

                # Claude 3.5 Sonnet pricing: $3 per 1M input, $15 per 1M output
                # Cache creation tokens count as input, cache read is 10% of input cost
                input_cost = (input_tokens + cache_creation_tokens) / 1_000_000 * 3.0
                output_cost = output_tokens / 1_000_000 * 15.0
                cache_read_cost = cache_read_tokens / 1_000_000 * 0.3  # 10% of input cost

                claude_cost += input_cost + output_cost + cache_read_cost

        return bq_cost + claude_cost

    def _is_rejected(self, audit_events: list[dict]) -> bool:
        """Check if request was rejected at any gate."""
        return any(event.get("decision") == "rejected" for event in audit_events)

    def _is_clarified(self, audit_events: list[dict]) -> bool:
        """Check if request triggered clarification."""
        for event in audit_events:
            if event.get("event_type") == "intent_classified":
                # Would need to check if clarification was requested
                pass
        return False

    def _is_sql_success(self, trace_events: list[dict]) -> bool | None:
        """Check if SQL validation succeeded from trace events."""
        for event in trace_events:
            if event.get("event") == "validate_sql.completed":
                return event.get("allowed", False)
        return None

    def _extract_retrieval_coverage(self, audit_events: list[dict]) -> float:
        """Extract retrieval coverage percentage (0-1)."""
        for event in audit_events:
            if event.get("event_type") == "retrieval":
                # Could extract coverage metric if available
                pass
        return -1  # Unknown

    def _extract_timestamp(self, audit_events: list[dict]) -> str | None:
        """Extract ISO timestamp from audit events."""
        if audit_events:
            return audit_events[0].get("timestamp")
        return None
