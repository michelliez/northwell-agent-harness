# Metrics & Analytics Dashboard

The Clarity Agent provides comprehensive metrics and analytics to monitor performance, costs, and quality. View metrics via an interactive dashboard or access them programmatically via API.

## Accessing Metrics

### Interactive Dashboard

Open the web browser to the metrics dashboard:

```
http://localhost:8000/metrics
```

The dashboard displays:
- **KPI Cards**: Key metrics at a glance (rejection rate, latency, success rate, cost)
- **Query Volume Trend**: Queries and latency over time
- **Quality Metrics**: SQL success rate, policy approval rate, retrieval coverage
- **Requests by Intent**: Distribution of intents (pie chart)
- **Cost Trend**: BigQuery cost estimates over time

**Period Selection**: Toggle between 7-day, 30-day, and 90-day views.

### API Endpoints

All metrics endpoints are under `/api/v1/metrics`:

#### GET `/api/v1/metrics`

Get comprehensive aggregated metrics for a time period.

**Query Parameters:**
- `days` (default: 7, max: 365) - Number of days to look back
- `start_date` (optional) - ISO date string (YYYY-MM-DD) to override `days`
- `end_date` (optional) - ISO date string (YYYY-MM-DD)

**Response Example:**

```json
{
  "total_queries": 42,
  "total_tokens": 125000,
  "avg_latency_ms": 1234.5,
  "policy_rejection_rate": 4.8,
  "sql_generation_success_rate": 92.3,
  "estimated_bigquery_cost": 0.45,
  "by_intent": {
    "general": {
      "count": 30,
      "avg_latency_ms": 1100,
      "success_rate": 93.3,
      "rejection_rate": 3.3,
      "avg_tokens": 2500
    }
  },
  "by_date": {
    "2025-08-15": {
      "queries": 6,
      "total_tokens": 18000,
      "avg_latency_ms": 1250,
      "rejections": 0,
      "clarifications": 1,
      "estimated_cost": 0.06,
      "success_rate": 90.0
    }
  }
}
```

#### GET `/api/v1/metrics/summary`

Get high-level summary metrics for executive dashboards.

```bash
curl http://localhost:8000/api/v1/metrics/summary?days=7
```

**Response:**

```json
{
  "summary": {
    "total_queries": 42,
    "avg_latency_ms": 1234.5,
    "policy_rejection_rate": 4.8,
    "sql_success_rate": 92.3,
    "estimated_cost": 0.45
  },
  "period": "last 7 days",
  "period_start": "2025-08-08T10:00:00",
  "period_end": "2025-08-15T18:00:00"
}
```

#### GET `/api/v1/metrics/by-intent`

Get metrics broken down by detected user intent.

```bash
curl http://localhost:8000/api/v1/metrics/by-intent?days=30
```

**Response:**

```json
{
  "intents": {
    "general": {
      "count": 85,
      "avg_latency_ms": 1150,
      "success_rate": 91.8,
      "rejection_rate": 3.5,
      "avg_tokens": 2400
    },
    "specification": {
      "count": 12,
      "avg_latency_ms": 1600,
      "success_rate": 75.0,
      "rejection_rate": 8.3,
      "avg_tokens": 3100
    }
  },
  "period": "last 30 days"
}
```

#### GET `/api/v1/metrics/by-date`

Get metrics broken down by date.

```bash
curl http://localhost:8000/api/v1/metrics/by-date?days=14
```

#### POST `/api/v1/metrics/refresh`

Force a refresh of metrics by rescanning all traces and audit logs.

```bash
curl -X POST http://localhost:8000/api/v1/metrics/refresh
```

This is normally unnecessary, but useful after bulk imports or if you suspect data consistency issues.

## Understanding the Metrics

### Key Performance Indicators

| Metric | What It Means | Target | How It's Used |
|--------|---------------|--------|---------------|
| **Avg Latency** | Average time from question to answer (ms) | < 2000ms | Monitor system responsiveness |
| **Policy Rejection Rate** (%) | Requests blocked by policy gates | < 10% | Verify policy is reasonable |
| **SQL Success Rate** (%) | Successfully generated and validated SQL | > 80% | Track planning quality |
| **Clarification Rate** (%) | Requests requiring user clarification | < 20% | Monitor intent classification |
| **Est. BigQuery Cost** | Estimated cost of SQL dry-runs | Track budget | Control query costs |

### Metric Definitions

**Total Queries**: Count of all executed queries in the period.

**Total Tokens**: Sum of tokens used across all LLM API calls (Claude API). Used to estimate API costs.

**Avg Latency**: Average time from question submission to final answer, in milliseconds. Includes policy checks, retrieval, planning, validation, and generation.

**Policy Rejection Rate**: Percentage of queries rejected at policy gates (input screening, output screening, cost approval). Lower is better if policies are well-calibrated.

**Clarification Rate**: Percentage of queries that triggered clarification interrupts (user had to answer questions). Indicates intent classification uncertainty.

**SQL Generation Success Rate**: Percentage of SQL planning attempts that passed validation. High percentage = good planning and validation.

**Avg Retrieval Coverage**: Percentage of available documentation successfully retrieved (0-100). Indicates how well the RAG index is helping the planner.

**Estimated BigQuery Cost**: Projected cost based on query dry-runs, using a nominal $6.25/TB rate. Only populated if BigQuery integration is enabled.

**By Intent**: Metrics grouped by the classified user intent. Allows comparison of performance across different question types.

**By Date**: Daily breakdown of metrics. Useful for trend analysis and identifying periods of degradation.

## For VP Presentations

Extract a metrics snapshot and share in a business-friendly format:

### Get a snapshot for the past 30 days:

```bash
curl http://localhost:8000/api/v1/metrics/summary?days=30 | jq .
```

### Export to Markdown report:

```python
from metrics.aggregator import MetricsAggregator
from metrics.exporter import export_markdown

aggregator = MetricsAggregator()
metrics = aggregator.get_metrics(days=30)
report = export_markdown(metrics)
print(report)
```

### Key talking points:

1. **Cost Efficiency**: "At $X per query, we're well under the SQL writing labor cost."
2. **Reliability**: "Policy gates rejected only Y% of requests—safe and permissive."
3. **Performance**: "Sub-2-second average latency provides a seamless user experience."
4. **Quality**: "Z% of generated SQL passes validation on the first try."
5. **Coverage**: "Retrieved documentation helps the agent in A% of cases."

## Metrics Architecture

Metrics are **aggregated on-demand** from two sources:

1. **Traces** (`.local/traces/*.jsonl`): Detailed execution logs with timing information
   - Every step of the pipeline is logged with nanosecond-precision timestamps
   - Traces are immutable and never modified after recording

2. **Audit Logs** (`.local/audit_logs/*.jsonl`): Policy decisions and SQL details
   - Records what was allowed/rejected and why
   - Includes cost estimates and SQL statements
   - Structured for compliance and auditing

The aggregator:
- Reads all available traces and audit logs
- Filters by date range
- Computes statistics (counts, averages, rates)
- Returns results in JSON format

**No database is required.** Metrics are computed on-the-fly from the immutable log files.

## Performance Considerations

- Aggregating metrics across 1000+ runs takes ~1-2 seconds
- Filtered queries (e.g., last 7 days) are much faster
- Dashboard updates happen when you refresh the browser or click a period button
- For real-time metrics, refresh the dashboard periodically

## Troubleshooting

### No data appears in metrics

1. Verify traces exist:
   ```bash
   ls .local/traces/
   ls .local/audit_logs/
   ```

2. Check that you've run at least one query through the agent

3. Verify the date range is correct (by default, last 7 days)

### Metrics seem incomplete

- Ensure `TRACE_CONTENT_MODE` is set (default: `metadata`)
- Traces must be written to `.local/traces/` (configurable via `ARTIFACT_PATH`)
- Audit logs must be written to `.local/audit_logs/` (also via `ARTIFACT_PATH`)

### API endpoint returns error

Check that metrics files are readable:

```bash
head -1 .local/audit_logs/*.jsonl  # Should show valid JSON
head -1 .local/traces/*.jsonl      # Should show valid JSON
```

## Related Documentation

- [Trace Format](../docs/reference/tracing.md) - Details on trace event structure
- [Audit Logging](../docs/reference/auditing.md) - Audit event reference
- [Docker Setup](./DOCKER.md) - Running metrics in containers
