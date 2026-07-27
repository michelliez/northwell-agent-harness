from __future__ import annotations

import argparse
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from retrieval.indexer import CHUNK_HARD_MAX_CHARS, estimate_tokens_from_chars

DEFAULT_RAG_DB_PATH = Path(".local/rag/index.sqlite")

NEAR_EMPTY_CHARS = 200  # below this, a chunk is too small to be useful (~50 tokens)


@dataclass(frozen=True)
class AuditReport:
    db_path: Path
    doc_count: int
    chunk_count: int
    char_p50: int
    char_p90: int
    char_p95: int
    char_p99: int
    char_max: int
    empty_count: int
    near_empty_count: int
    duplicate_text_hash_count: int
    over_limit_count: int
    by_category: dict[str, int]
    section_fact_count: int


def _pct(sorted_vals: list[int], p: int) -> int:
    """p-th percentile (1–100) from a sorted list using the nearest-rank method."""
    if not sorted_vals:
        return 0
    if not 1 <= p <= 100:
        raise ValueError("p must be between 1 and 100")
    idx = math.ceil(len(sorted_vals) * p / 100) - 1
    return sorted_vals[idx]


def run_audit(db_path: Path) -> AuditReport:
    if not db_path.is_file():
        raise SystemExit(
            f"Database not found: {db_path}\nBuild the index first with agent-harness-rag-index."
        )

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()
        doc_count: int = cur.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        section_fact_count: int = cur.execute("SELECT COUNT(*) FROM section_facts").fetchone()[0]

        rows = cur.execute(
            "SELECT category, text, text_hash FROM chunks ORDER BY length(text)"
        ).fetchall()
    finally:
        conn.close()

    chunk_count = len(rows)
    char_lengths = [len(r["text"]) for r in rows]

    empty_count = sum(1 for r in rows if not r["text"].strip() or r["text"].strip() == "No data")
    near_empty_count = sum(
        1
        for r in rows
        if 0 < len(r["text"].strip()) < NEAR_EMPTY_CHARS and r["text"].strip() != "No data"
    )

    hashes = [r["text_hash"] for r in rows]
    duplicate_text_hash_count = chunk_count - len(set(hashes))

    over_limit_count = sum(1 for n in char_lengths if n > CHUNK_HARD_MAX_CHARS)

    by_category: dict[str, int] = {}
    for r in rows:
        cat = r["category"]
        by_category[cat] = by_category.get(cat, 0) + 1

    return AuditReport(
        db_path=db_path,
        doc_count=doc_count,
        chunk_count=chunk_count,
        char_p50=_pct(char_lengths, 50),
        char_p90=_pct(char_lengths, 90),
        char_p95=_pct(char_lengths, 95),
        char_p99=_pct(char_lengths, 99),
        char_max=char_lengths[-1] if char_lengths else 0,
        empty_count=empty_count,
        near_empty_count=near_empty_count,
        duplicate_text_hash_count=duplicate_text_hash_count,
        over_limit_count=over_limit_count,
        by_category=by_category,
        section_fact_count=section_fact_count,
    )


def print_report(report: AuditReport) -> None:
    sep = "=" * 62
    n = report.chunk_count

    def pct_str(count: int) -> str:
        if n == 0:
            return ""
        return f"  ({100 * count / n:4.1f}%)"

    print(f"\n{sep}")
    print(f"  RAG Corpus Audit: {report.db_path}")
    print(sep)

    print("\nCorpus")
    print(f"  Documents                    : {report.doc_count:,}")
    print(f"  Chunks                       : {report.chunk_count:,}")
    print(f"  Present-but-unavailable facts: {report.section_fact_count:,}")

    print("\nSize distribution  (chars / ~tokens at 4 chars/token)")
    for label, val in [
        ("p50", report.char_p50),
        ("p90", report.char_p90),
        ("p95", report.char_p95),
        ("p99", report.char_p99),
        ("max", report.char_max),
    ]:
        tok = estimate_tokens_from_chars(val)
        flag = "  *** OVER LIMIT" if val > CHUNK_HARD_MAX_CHARS else ""
        print(f"  {label}  : {val:6,} chars  ~{tok:5,} tok{flag}")

    print("\nProblem chunks")
    print(
        f"  Empty or 'No data'              : {report.empty_count:5,}{pct_str(report.empty_count)}"
    )
    print(
        f"  Near-empty (< {NEAR_EMPTY_CHARS} chars)          : "
        f"{report.near_empty_count:5,}{pct_str(report.near_empty_count)}"
    )
    print(f"  Duplicate text_hash             : {report.duplicate_text_hash_count:5,}")
    print(
        f"  Over {CHUNK_HARD_MAX_CHARS:,} chars (~1,200 tok)    : "
        f"{report.over_limit_count:5,}{pct_str(report.over_limit_count)}"
    )

    print("\nBy category")
    for cat, count in sorted(report.by_category.items(), key=lambda x: -x[1]):
        print(f"  {cat:<22}: {count:6,}{pct_str(count)}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit an existing RAG index before producing embeddings."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("RAG_DB_PATH") or DEFAULT_RAG_DB_PATH),
        help="Path to the SQLite index (default: RAG_DB_PATH env or .local/rag/index.sqlite)",
    )
    args = parser.parse_args()
    report = run_audit(args.db)
    print_report(report)


if __name__ == "__main__":
    main()
