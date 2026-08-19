"""Streamlit metrics dashboard for the Clarity Agent."""

from __future__ import annotations

import os

import requests
import streamlit as st

st.set_page_config(
    page_title="Northwell Clarity Agent Metrics",
    page_icon="",
    layout="wide",
)

# API endpoint
API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
METRICS_ENDPOINT = f"{API_URL}/api/v1/metrics"

st.title("Northwell Clarity Agent Metrics")
st.markdown("Performance, cost, and quality analytics for the Epic Clarity agent")

# Refresh button at the top
col1, col2, col3 = st.columns([1, 1, 1])
with col2:
    if st.button("Refresh Metrics", use_container_width=True):
        st.rerun()

# Period selector
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Last 7 Days", use_container_width=True):
        st.session_state.days = 7
with col2:
    if st.button("Last 30 Days", use_container_width=True):
        st.session_state.days = 30
with col3:
    if st.button("Last 90 Days", use_container_width=True):
        st.session_state.days = 90

# Default to 7 days
if "days" not in st.session_state:
    st.session_state.days = 7

days = st.session_state.days

# Fetch metrics
try:
    response = requests.get(f"{METRICS_ENDPOINT}?days={days}", timeout=10)
    response.raise_for_status()
    metrics = response.json()
except requests.exceptions.RequestException as e:
    st.error(f"Could not connect to API: {e}")
    st.info(f"Make sure the API is running at {API_URL}")
    st.stop()

# Display KPIs
st.subheader("Key Performance Indicators")
kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5, kpi_col6 = st.columns(6)

with kpi_col1:
    st.metric(
        "Total Queries",
        f"{metrics['total_queries']}",
        help="Total number of queries executed",
    )

with kpi_col2:
    latency = metrics["avg_latency_ms"]
    st.metric(
        "Avg Latency",
        f"{latency:.0f}ms",
        delta=f"{'Good' if latency < 1000 else 'OK' if latency < 2000 else 'High'}",
        delta_color="inverse",
        help="Average time from question to answer",
    )

with kpi_col3:
    rejection_rate = metrics["policy_rejection_rate"]
    st.metric(
        "Policy Rejections",
        f"{rejection_rate:.1f}%",
        help="Percentage of requests blocked by policy",
    )

with kpi_col4:
    sql_rate = metrics["sql_generation_success_rate"]
    st.metric(
        "SQL Success Rate",
        f"{sql_rate:.1f}%",
        delta=f"{sql_rate:.1f}% {'Good' if sql_rate > 80 else 'OK' if sql_rate > 60 else 'Low'}",
        delta_color="normal",
        help="Successfully generated and validated SQL",
    )

with kpi_col5:
    cost = metrics["estimated_bigquery_cost"]
    st.metric(
        "Est. BQ Cost",
        f"${cost:.2f}",
        delta=f"{'Good' if cost < 10 else 'OK' if cost < 50 else 'High'}",
        delta_color="inverse",
        help="Estimated BigQuery cost",
    )

with kpi_col6:
    st.metric(
        "Total Tokens",
        f"{metrics['total_tokens']:,}",
        help="Total tokens used across all queries",
    )

# Charts
st.divider()
st.subheader("Trends & Breakdowns")

chart_col1, chart_col2 = st.columns(2)

# Latency and query volume trend
with chart_col1:
    st.markdown("#### Query Volume & Latency Trend")
    if metrics["by_date"]:
        dates = sorted(metrics["by_date"].keys())
        latencies = [metrics["by_date"][d]["avg_latency_ms"] for d in dates]
        queries = [metrics["by_date"][d]["queries"] for d in dates]

        chart_data = {
            "Date": dates,
            "Latency (ms)": latencies,
            "Queries": queries,
        }

        st.line_chart(
            {d: metrics["by_date"][d]["avg_latency_ms"] for d in dates},
            use_container_width=True,
        )
    else:
        st.info("No data available yet. Ask some questions to see metrics!")

# Quality metrics
with chart_col2:
    st.markdown("#### Quality Metrics")
    if metrics["total_queries"] > 0:
        quality_data = {
            "SQL Success": metrics["sql_generation_success_rate"],
            "Policy Approval": 100 - metrics["policy_rejection_rate"],
            "Retrieval": metrics["avg_retrieval_coverage"] * 100,
        }
        st.bar_chart(quality_data, use_container_width=True)
    else:
        st.info("No data available yet. Ask some questions to see metrics!")

# Intent breakdown
st.divider()
intent_col, cost_col = st.columns(2)

with intent_col:
    st.markdown("#### Requests by Intent")
    if metrics["by_intent"]:
        intent_data = {
            intent: metrics["by_intent"][intent]["count"] for intent in metrics["by_intent"]
        }
        st.bar_chart(intent_data, use_container_width=True)
    else:
        st.info("No intent data yet.")

with cost_col:
    st.markdown("#### Cost Trend")
    if metrics["by_date"]:
        cost_data = {
            d: metrics["by_date"][d]["estimated_cost"] for d in sorted(metrics["by_date"].keys())
        }
        st.line_chart(cost_data, use_container_width=True)
    else:
        st.info("No cost data yet.")

# Node performance
st.divider()
st.subheader("Workflow Node Performance")

if metrics.get("by_node"):
    node_col1, node_col2 = st.columns(2)

    with node_col1:
        st.markdown("#### Node Latency Timeline")
        nodes = sorted(metrics["by_node"].keys())
        latencies = [metrics["by_node"][n]["avg_latency_ms"] for n in nodes]
        st.bar_chart(
            {node: metrics["by_node"][node]["avg_latency_ms"] for node in nodes},
            use_container_width=True,
        )

    with node_col2:
        st.markdown("#### Node Success Rates")
        st.bar_chart(
            {node: metrics["by_node"][node]["success_rate"] for node in nodes},
            use_container_width=True,
        )

    st.markdown("#### Node Detailed Metrics")
    node_data = []
    for node in sorted(metrics["by_node"].keys()):
        n = metrics["by_node"][node]
        node_data.append(
            {
                "Node": n["node_name"],
                "Count": n["count"],
                "Timed Runs": n.get("timed_count", 0),
                "Avg Latency (ms)": f"{n['avg_latency_ms']:.0f}",
                "Min (ms)": f"{n['min_latency_ms']:.0f}",
                "Max (ms)": f"{n['max_latency_ms']:.0f}",
                "Success": n["success_count"],
                "Failures": n["failure_count"],
                "Success Rate": f"{n['success_rate']:.1f}%",
                "Total Tokens": n["total_tokens"],
                "Input Tokens": n["input_tokens"],
                "Output Tokens": n["output_tokens"],
                "Cache Created": n["cache_creation_tokens"],
                "Cache Read": n["cache_read_tokens"],
            }
        )
    st.dataframe(node_data, use_container_width=True)

    st.markdown("#### Token Breakdown by Node")
    token_col1, token_col2 = st.columns(2)

    with token_col1:
        st.markdown("**Total Tokens per Node**")
        st.bar_chart(
            {
                node: metrics["by_node"][node]["total_tokens"]
                for node in sorted(metrics["by_node"].keys())
            },
            use_container_width=True,
        )

    with token_col2:
        st.markdown("**Token Type Breakdown**")
        token_types = {
            "Input": sum(n["input_tokens"] for n in metrics["by_node"].values()),
            "Output": sum(n["output_tokens"] for n in metrics["by_node"].values()),
            "Cache Created": sum(n["cache_creation_tokens"] for n in metrics["by_node"].values()),
            "Cache Read": sum(n["cache_read_tokens"] for n in metrics["by_node"].values()),
        }
        st.bar_chart(token_types, use_container_width=True)
else:
    st.info("No node performance data yet.")

# Detailed tables
st.divider()
st.subheader("Detailed Breakdown")

tab1, tab2 = st.tabs(["By Date", "By Intent"])

with tab1:
    if metrics["by_date"]:
        daily_data = []
        for date in sorted(metrics["by_date"].keys()):
            d = metrics["by_date"][date]
            daily_data.append(
                {
                    "Date": date,
                    "Queries": d["queries"],
                    "Avg Latency (ms)": f"{d['avg_latency_ms']:.0f}",
                    "Total Tokens": d["total_tokens"],
                    "Rejections": d["rejections"],
                    "Clarifications": d["clarifications"],
                    "Est. Cost": f"${d['estimated_cost']:.2f}",
                    "Success Rate": f"{d['success_rate']:.1f}%",
                }
            )
        st.dataframe(daily_data, use_container_width=True)
    else:
        st.info("No daily data available.")

with tab2:
    if metrics["by_intent"]:
        intent_data = []
        for intent in sorted(metrics["by_intent"].keys()):
            i = metrics["by_intent"][intent]
            intent_data.append(
                {
                    "Intent": i["intent"],
                    "Count": i["count"],
                    "Avg Latency (ms)": f"{i['avg_latency_ms']:.0f}",
                    "Success Rate": f"{i['success_rate']:.1f}%",
                    "Rejection Rate": f"{i['rejection_rate']:.1f}%",
                    "Avg Tokens": i["avg_tokens"],
                }
            )
        st.dataframe(intent_data, use_container_width=True)
    else:
        st.info("No intent data available.")

# Footer
st.divider()
col1, col2, col3 = st.columns([1, 1, 1])
with col3:
    last_updated = metrics.get("last_updated", "Unknown")
    st.caption(f"Last updated: {last_updated}")
