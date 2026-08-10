"""Rebuild an index's FTS table with a new doc2query expansion corpus.

Reparsing 40K HTML documents takes hours; swapping the ``generated_queries``
column does not need it. This copies a finished source index and rebuilds only
``chunks_fts`` from the copy's own chunks plus the supplied corpus, so chunk
IDs (and every dataset keyed by them) are untouched by construction.

The corpus is grouped JSONL: one row per chunk with ``chunk_id`` and a
``queries`` list of ``{"query": ...}`` entries. Provenance is stamped into
``index_metadata`` (corpus hash, counts, base index version) and the derived
``index_version`` changes with the corpus, so two indexes built from different
corpora can never claim the same identity.

Usage:
    uv run agent-harness-rag-expansion-migrate \
        <source-index.sqlite> <corpus.jsonl> <target-index.sqlite>

An empty-string corpus path builds the control arm: same schema, no expansion
text.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from retrieval.index_contract import INDEX_SCHEMA_VERSION

BATCH_SIZE = 5000


def load_corpus(corpus_path: Path) -> tuple[dict[str, str], int, str]:
    raw_bytes = corpus_path.read_bytes()
    expansion_by_chunk: dict[str, str] = {}
    query_count = 0
    for line in raw_bytes.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        queries = [str(entry["query"]) for entry in row["queries"]]
        expansion_by_chunk[str(row["chunk_id"])] = "\n".join(queries)
        query_count += len(queries)
    return expansion_by_chunk, query_count, hashlib.sha256(raw_bytes).hexdigest()


def migrate(
    source_db: Path,
    target: Path,
    expansion_by_chunk: dict[str, str],
    corpus_hash: str,
) -> None:
    print(f"=== {target.name} ===", flush=True)
    shutil.copyfile(source_db, target)
    conn = sqlite3.connect(target)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE chunks_fts")
        conn.execute(
            """CREATE VIRTUAL TABLE chunks_fts USING fts5(
                   chunk_id, source_path, title, category, heading_path, text,
                   generated_queries
               )"""
        )
        read_cur = conn.cursor()
        read_cur.execute(
            """
            SELECT c.chunk_id, d.source_path, d.title, c.category, c.heading_path, c.text
            FROM chunks AS c
            JOIN docs AS d ON d.doc_id = c.doc_id
            """
        )
        inserted = 0
        expanded = 0
        while True:
            rows = read_cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            batch = []
            for chunk_id, source_path, title, category, heading_path, text in rows:
                extra = expansion_by_chunk.get(str(chunk_id), "")
                if extra:
                    expanded += 1
                batch.append((chunk_id, source_path, title, category, heading_path, text, extra))
            conn.executemany(
                """INSERT INTO chunks_fts
                   (chunk_id, source_path, title, category, heading_path, text,
                    generated_queries)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                batch,
            )
            inserted += len(batch)
            if inserted % 100000 < BATCH_SIZE:
                print(f"  {inserted:,} FTS rows rebuilt", flush=True)

        # Every corpus row must land on a live chunk; a partial match means the
        # corpus was generated against a different chunker version.
        if expansion_by_chunk and expanded != len(expansion_by_chunk):
            sys.exit(f"expected {len(expansion_by_chunk):,} expanded chunks, matched {expanded:,}")

        old_version = conn.execute(
            "SELECT value FROM index_metadata WHERE key = 'index_version'"
        ).fetchone()[0]
        derived_version = hashlib.sha256(
            f"{old_version}:{INDEX_SCHEMA_VERSION}:{corpus_hash}".encode()
        ).hexdigest()
        query_count = sum(text.count("\n") + 1 for text in expansion_by_chunk.values())
        conn.executemany(
            "INSERT OR REPLACE INTO index_metadata (key, value) VALUES (?, ?)",
            [
                ("schema_version", INDEX_SCHEMA_VERSION),
                ("index_version", derived_version),
                ("expansion_corpus_hash", corpus_hash),
                ("expansion_query_count", str(query_count)),
                ("expansion_chunk_count", str(len(expansion_by_chunk))),
                ("expansion_base_index_version", str(old_version)),
            ],
        )
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
        conn.execute("COMMIT")
    finally:
        conn.close()
    print(f"  {inserted:,} rows, {expanded:,} expanded -> {target.name}")
    print(f"  index_version: {derived_version}")


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(
            "usage: agent-harness-rag-expansion-migrate "
            "<source-index.sqlite> <corpus.jsonl> <target-index.sqlite>"
        )
    source_db = Path(sys.argv[1])
    corpus_path = Path(sys.argv[2]) if sys.argv[2] else None
    target = Path(sys.argv[3])
    if not source_db.is_file():
        sys.exit(f"missing source index: {source_db}")
    if target.resolve() == source_db.resolve():
        sys.exit("target must differ from the source index")
    if corpus_path is not None:
        if not corpus_path.is_file():
            sys.exit(f"missing expansion corpus: {corpus_path}")
        expansion_by_chunk, query_count, corpus_hash = load_corpus(corpus_path)
        print(f"Corpus: {query_count:,} texts over {len(expansion_by_chunk):,} chunks")
    else:
        expansion_by_chunk, corpus_hash = {}, hashlib.sha256(b"").hexdigest()
        print("No corpus supplied; building the empty control arm")
    migrate(source_db, target, expansion_by_chunk, corpus_hash)


if __name__ == "__main__":
    main()
