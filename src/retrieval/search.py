"""Direct SQLite search extracted from the former RAG MCP server.

All public functions operate on a caller-supplied SQLite connection so they
can be used in both in-process calls and tests with temporary databases.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from retrieval.index_contract import INDEX_CHUNKER_VERSION, INDEX_SCHEMA_VERSION

STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "can",
    "could",
    "do",
    "does",
    "document",
    "documentation",
    "documents",
    "find",
    "for",
    "hold",
    "holds",
    "i",
    "info",
    "information",
    "is",
    "look",
    "looking",
    "mean",
    "means",
    "me",
    "of",
    "on",
    "or",
    "please",
    "represent",
    "represents",
    "say",
    "says",
    "show",
    "tell",
    "table",
    "tables",
    "the",
    "to",
    "use",
    "used",
    "what",
    "which",
    "would",
}

MAX_FTS_QUERY_TOKENS = 8
MAX_RAG_TOP_K = 25

# Short prefix terms match a large fraction of the index: measured against the
# live index, "m"* spans 511,742 chunk-term rows and "a"* spans 905,255, while
# "hyperspace"* spans 651. Most short tokens are contractions or filler, but a
# few domain acronyms are useful. Keep those and query them exactly, never as
# prefixes. Tokens containing a digit also survive normalization so exact codes
# can be considered, but their index evidence decides whether they stay.
MIN_FTS_TOKEN_CHARS = 3
SHORT_EXACT_TERMS = frozenset({"dx", "ed", "er", "ip", "or", "rx"})

# fts5vocab exposes the FTS index's own per-term document counts. It is created
# in `temp` so the main database stays open read-only, and it needs no rebuild.
FTS_VOCAB_TABLE = "chunks_fts_vocab"

# Results are chunk-level and were previously ungrouped, so one verbose document
# could take the entire budget: a probe for "patient primary care provider"
# returned five chunks of DM_CANCER_PATIENT_HX out of eight. That is correct when
# the document is the answer and useless when it is not. Take the best few chunks
# per document first, then backfill, so a single-document match still fills the
# budget while a broad question sees more than one table.
MAX_CHUNKS_PER_DOCUMENT = 3

# Diversity needs candidates to choose between; the caller's top_k alone leaves
# nothing to trim.
CANDIDATE_POOL_MULTIPLIER = 4


# The trailing weight scores the generated_queries doc2query column. It sits
# below the text weight so expansion vocabulary can bridge analyst phrasing the
# documentation lacks, but can never outshout documentation matches: at parity
# the pilot showed expansion text on competing tables stealing ranks from a
# document that matched on its own words.
FTS_BM25_WEIGHTS = (0.0, 8.0, 10.0, 0.5, 5.0, 1.0, 0.5)
DOCUMENT_HINT_EXCLUSIONS = frozenset({"EHI", "ETL", "INI", "SQL"})

REQUIRED_INDEX_METADATA = frozenset(
    {
        "index_version",
        "schema_version",
        "parser_version",
        "chunker_version",
        "chunk_target_chars",
        "chunk_hard_max_chars",
        "doc_count",
        "chunk_count",
        "section_fact_count",
        "hierarchy_node_count",
        "relationship_count",
        "resolved_relationship_count",
    }
)

DEFAULT_INDEX_PATH = Path(".local/rag/index.sqlite")


def _compatibility_error(metadata: dict[str, str], db_path: Path) -> str | None:
    expected_versions = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "chunker_version": INDEX_CHUNKER_VERSION,
    }
    for key, expected in expected_versions.items():
        actual = metadata.get(key)
        if actual != expected:
            return (
                f"RAG database {db_path} uses incompatible {key} "
                f"{actual!r}; expected {expected!r}. Rebuild the index."
            )
    return None


def open_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise RuntimeError(f"RAG index does not exist: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        metadata = {
            str(row["key"]): str(row["value"])
            for row in conn.execute(
                "SELECT key, value FROM index_metadata "
                "WHERE key IN ('schema_version', 'chunker_version')"
            ).fetchall()
            if row["value"]
        }
        compatibility_error = _compatibility_error(metadata, db_path)
        if compatibility_error:
            raise RuntimeError(compatibility_error)
    except (RuntimeError, sqlite3.DatabaseError) as exc:
        if "conn" in locals():
            conn.close()
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"RAG database {db_path} is corrupt or unreadable: {exc}") from exc
    return conn


def validate_index(db_path: Path) -> None:
    """Validate the RAG database. Raises SystemExit on any failure."""
    if not db_path.is_file():
        raise SystemExit(f"RAG database does not exist: {db_path}")
    if db_path.stat().st_size == 0:
        raise SystemExit(f"RAG database is empty (0 bytes): {db_path}")

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Cannot open RAG database {db_path}: {exc}") from exc

    try:
        cur = conn.cursor()
        present = {
            row["name"]
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        missing = {
            "docs",
            "chunks",
            "chunks_fts",
            "index_metadata",
            "section_facts",
            "nodes",
            "table_relationships",
        } - present
        if missing:
            raise SystemExit(
                f"RAG database {db_path} is missing tables: {', '.join(sorted(missing))}"
            )

        metadata = {
            str(row["key"]): str(row["value"])
            for row in cur.execute("SELECT key, value FROM index_metadata").fetchall()
            if row["value"]
        }
        missing_meta = REQUIRED_INDEX_METADATA - metadata.keys()
        if missing_meta:
            raise SystemExit(
                f"RAG database {db_path} is missing index metadata: "
                f"{', '.join(sorted(missing_meta))}"
            )

        compatibility_error = _compatibility_error(metadata, db_path)
        if compatibility_error:
            raise SystemExit(compatibility_error)

        doc_count = cur.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunk_count = cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        section_fact_count = cur.execute("SELECT COUNT(*) FROM section_facts").fetchone()[0]
        hierarchy_node_count = cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        relationship_count = cur.execute("SELECT COUNT(*) FROM table_relationships").fetchone()[0]
        resolved_relationship_count = cur.execute(
            "SELECT COUNT(*) FROM table_relationships WHERE target_doc_id IS NOT NULL"
        ).fetchone()[0]
        if doc_count < 1:
            raise SystemExit(f"RAG database {db_path} contains no indexed documents")
        if chunk_count < 1:
            raise SystemExit(f"RAG database {db_path} contains no indexed chunks")
        if metadata["doc_count"] != str(doc_count):
            raise SystemExit(f"RAG database {db_path} document count does not match metadata")
        if metadata["chunk_count"] != str(chunk_count):
            raise SystemExit(f"RAG database {db_path} chunk count does not match metadata")
        if metadata["section_fact_count"] != str(section_fact_count):
            raise SystemExit(f"RAG database {db_path} section fact count does not match metadata")
        if hierarchy_node_count < doc_count + chunk_count:
            raise SystemExit(f"RAG database {db_path} contains an incomplete node hierarchy")
        if metadata["hierarchy_node_count"] != str(hierarchy_node_count):
            raise SystemExit(f"RAG database {db_path} hierarchy node count does not match metadata")
        if metadata["relationship_count"] != str(relationship_count):
            raise SystemExit(f"RAG database {db_path} relationship count does not match metadata")
        if metadata["resolved_relationship_count"] != str(resolved_relationship_count):
            raise SystemExit(
                f"RAG database {db_path} resolved relationship count does not match metadata"
            )
    except SystemExit:
        raise
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"RAG database {db_path} is corrupt or unreadable: {exc}") from exc
    finally:
        conn.close()


def is_identifier_token(token: str) -> bool:
    """Identifier-like tokens are the highest-value terms this corpus contains.

    `a0h_delete` matches three chunks; `access` matches 5,690. Underscores and a
    mixture of letters and digits mark a likely Clarity table or column name.
    All-digit tokens are values or codes, not schema identifiers.
    """
    has_digit = any(character.isdigit() for character in token)
    has_alpha = any(character.isalpha() for character in token)
    return "_" in token or (has_digit and has_alpha)


def uses_exact_fts_term(token: str) -> bool:
    """Return whether a token should be matched exactly rather than as a prefix."""
    return is_identifier_token(token) or token.isdigit() or token in SHORT_EXACT_TERMS


def fts5_term(token: str) -> str:
    """Render one normalized token using its single authoritative match mode."""
    quoted = f'"{token}"'
    return quoted if uses_exact_fts_term(token) else f"{quoted}*"


def normalize_search_token(token: str) -> str | None:
    normalized = token.casefold()
    # Lowercase "or" is syntax; uppercase "OR" is the clinical operating-room
    # acronym and belongs to the exact-match short domain set.
    preserve_stopword = token == "OR"
    if normalized in STOPWORDS and not preserve_stopword:
        return None
    if (
        len(normalized) < MIN_FTS_TOKEN_CHARS
        and not is_identifier_token(normalized)
        and normalized not in SHORT_EXACT_TERMS
    ):
        return None
    if (
        normalized.isalpha()
        and len(normalized) > 4
        and normalized.endswith("s")
        and not normalized.endswith(("is", "ss", "us"))
    ):
        normalized = normalized[:-1]
    if normalized in STOPWORDS and not preserve_stopword:
        return None
    return normalized


def select_query_tokens(
    tokens: list[str],
    *,
    limit: int = MAX_FTS_QUERY_TOKENS,
    match_count: Callable[[str], int] | None = None,
) -> list[str]:
    """Keep up to `limit` evidence-backed selective tokens in query order.

    Truncating to the first `limit` tokens ranks by position in the sentence,
    which is unrelated to how much a term narrows the search: on the live
    benchmark the identifier `a0h_delete` survives as the eighth token by luck,
    and one more filler word ahead of it would have dropped the only term that
    identifies anything. Rank by estimated match volume instead, rarest first.

    A zero-count term cannot add a row to an OR query, so it is removed rather
    than treated as maximally selective. Identifier-like terms receive priority
    only after the index confirms that their exact phrase can match.

    Without a `match_count` provider this falls back to positional truncation,
    so callers holding no index still get a bounded query.
    """
    if match_count is None:
        return tokens[:limit]

    position = {token: index for index, token in enumerate(tokens)}
    counts = {token: match_count(token) for token in tokens}
    matching_tokens = [token for token in tokens if counts[token] > 0]

    def selectivity(token: str) -> tuple[int, int, int]:
        if is_identifier_token(token):
            return (0, 0, position[token])
        return (1, counts[token], position[token])

    keep = set(sorted(matching_tokens, key=selectivity)[:limit])
    return [token for token in matching_tokens if token in keep]


def search_tokens(
    query: str,
    *,
    match_count: Callable[[str], int] | None = None,
) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in re.findall(r"\w+", query):
        token = normalize_search_token(raw_token)
        if token is None or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
    return select_query_tokens(tokens, match_count=match_count)


def fts5_query(
    query: str,
    *,
    match_count: Callable[[str], int] | None = None,
) -> str:
    """Build the FTS5 MATCH expression, or an empty string if nothing survives.

    This is a disjunction. An earlier conjunctive pass ran first and its results
    were preferred, on the theory that a chunk containing every term is a better
    answer than one containing any term. It is: it just never happens. Requiring
    up to eight terms to co-occur inside a 333-character chunk returned zero rows
    for 50 of 50 benchmark queries, so the branch only ever cost a round trip.

    Requiring a *subset* -- the two or three rarest terms -- is possible, but it
    is deliberately outside the frozen lexical baseline.
    """
    terms = [fts5_term(token) for token in search_tokens(query, match_count=match_count)]
    return " OR ".join(terms)


def match_count_lookup(cur: sqlite3.Cursor) -> Callable[[str], int] | None:
    """Estimate how many FTS rows a token's emitted expression can reach.

    Exact terms and identifier phrases use their real MATCH row count. Prefix
    terms use the sum of the FTS vocabulary's per-term row counts across the
    prefix range. That is a fast posting-volume estimate, not an exact distinct
    row count: a row containing two matching vocabulary terms is counted twice.

    Returns None when the index predates fts5vocab support or the table is
    unavailable, which leaves token selection on its positional fallback.
    """
    try:
        cur.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.{FTS_VOCAB_TABLE} "
            "USING fts5vocab(main, chunks_fts, 'row')"
        )
    except sqlite3.DatabaseError:
        return None

    # A private cursor: lookups happen while the caller still holds result rows.
    vocab_cur = cur.connection.cursor()
    cache: dict[str, int] = {}

    def lookup(term: str) -> int:
        if term not in cache:
            if uses_exact_fts_term(term):
                row = vocab_cur.execute(
                    "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                    (fts5_term(term),),
                ).fetchone()
            else:
                upper_bound = term[:-1] + chr(ord(term[-1]) + 1)
                row = vocab_cur.execute(
                    f"SELECT COALESCE(SUM(doc), 0) FROM temp.{FTS_VOCAB_TABLE} "
                    "WHERE term >= ? AND term < ?",
                    (term, upper_bound),
                ).fetchone()
            cache[term] = int(row[0]) if row is not None else 0
        return cache[term]

    return lookup


def document_hint(query: str) -> str | None:
    candidates = [
        candidate
        for candidate in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", query)
        if candidate not in DOCUMENT_HINT_EXCLUSIONS
    ]
    table_match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*)\s+table\b", query)
    if table_match is not None:
        candidates.append(table_match.group(1).upper())
    if not candidates:
        return None
    return max(candidates, key=lambda c: ("_" in c, len(c)))


def normalize_lookup_text(text: str) -> str:
    return text.upper().replace(".HTML", "").replace(" ", "_").strip()


def get_index_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'index_version' LIMIT 1"
    ).fetchone()
    if row is None or not row["value"]:
        raise RuntimeError("RAG index is missing index_version metadata")
    return str(row["value"])


def fetch_chunk_by_id(
    cur: sqlite3.Cursor,
    chunk_id: str,
) -> dict | None:
    cur.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.heading_path, c.text, c.category,
               d.source_path, d.title
        FROM chunks c
        JOIN docs d ON c.doc_id = d.doc_id
        WHERE c.chunk_id = ?
        LIMIT 1
        """,
        (chunk_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "chunk_id": row["chunk_id"],
        "doc_id": row["doc_id"],
        "title": row["title"],
        "heading_path": row["heading_path"],
        "category": row["category"],
        "source_path": row["source_path"],
        "text": row["text"],
        "source": "rag_index",
    }


def keyword_search(
    cur: sqlite3.Cursor,
    *,
    fts_query: str,
    top_k: int,
    document_name_hint: str | None = None,
) -> list[dict]:
    hint = document_name_hint or ""
    cur.execute(
        """
        SELECT chunk_id, source_path, title, heading_path, text,
               CASE
                   WHEN ? != '' AND (
                       upper(source_path) = ?
                       OR upper(source_path) LIKE ?
                       OR upper(title) LIKE ?
                   ) THEN 0
                   ELSE 1
               END AS document_rank,
               bm25(chunks_fts, ?, ?, ?, ?, ?, ?, ?) AS score
        FROM chunks_fts
        WHERE chunks_fts MATCH ?
        ORDER BY document_rank, score
        LIMIT ?
        """,
        (
            hint,
            f"{hint}.HTML",
            f"%/{hint}.HTML",
            f"{hint} - %",
            *FTS_BM25_WEIGHTS,
            fts_query,
            top_k,
        ),
    )
    return [
        {
            "chunk_id": row["chunk_id"],
            "source_path": row["source_path"],
            "title": row["title"],
            "heading_path": row["heading_path"],
            "score": row["score"],
            "preview": row["text"][:300],
        }
        for row in cur.fetchall()
    ]


def document_key(result: dict) -> str:
    """Identify the document a chunk came from, for grouping."""
    return normalize_lookup_text(str(result.get("source_path") or result.get("title") or ""))


def apply_document_diversity(
    ranked: list[dict],
    *,
    top_k: int,
    max_per_document: int = MAX_CHUNKS_PER_DOCUMENT,
    exempt_document: str | None = None,
) -> list[dict]:
    """Cap chunks per document, then backfill with what the cap displaced.

    Rank order is preserved inside both passes. A query that genuinely matches a
    single document still fills the budget from it -- those chunks arrive in the
    backfill rather than the first pass -- so this trades ordering, not recall.

    `exempt_document` is the document the caller named outright. Asking about
    ABN_ORDERS should return ABN_ORDERS, so breadth is not wanted there and the
    cap does not apply to it.
    """
    if max_per_document < 1:
        return ranked[:top_k]

    exempt = normalize_lookup_text(exempt_document) if exempt_document else None
    kept: list[dict] = []
    displaced: list[dict] = []
    per_document: Counter[str] = Counter()

    for result in ranked:
        if len(kept) == top_k:
            break
        key = document_key(result)
        if (exempt is not None and key == exempt) or per_document[key] < max_per_document:
            per_document[key] += 1
            kept.append(result)
        else:
            displaced.append(result)

    if len(kept) < top_k:
        kept.extend(displaced[: top_k - len(kept)])
    return kept


def search_ranked_chunks(
    cur: sqlite3.Cursor,
    *,
    query: str,
    top_k: int,
    max_per_document: int = MAX_CHUNKS_PER_DOCUMENT,
) -> list[dict]:
    match_expression = fts5_query(query, match_count=match_count_lookup(cur))
    if not match_expression:
        return []

    # Gather a wider pool than requested so the diversity pass has something to
    # choose between, then trim it back to top_k.
    hint = document_hint(query)
    candidates = keyword_search(
        cur,
        fts_query=match_expression,
        top_k=top_k * CANDIDATE_POOL_MULTIPLIER,
        document_name_hint=hint,
    )

    return apply_document_diversity(
        candidates,
        top_k=top_k,
        max_per_document=max_per_document,
        exempt_document=hint,
    )


def expand_document_relationships(
    cur: sqlite3.Cursor,
    *,
    seed_document_ids: list[str],
    max_related_tables: int = 5,
) -> list[dict]:
    """Return a bounded one-hop graph expansion from ranked seed documents.

    Every returned edge originated in an explicit Foreign Key Information row.
    Expansion is deliberately one hop: this is deterministic graph lookup, not
    recursive retrieval or an LLM decision.
    """
    if max_related_tables < 1 or not seed_document_ids:
        return []

    expanded: list[dict] = []
    seen_neighbors: set[str] = set()
    for seed_rank, seed_doc_id in enumerate(seed_document_ids, start=1):
        rows = cur.execute(
            """SELECT r.relationship_id, r.source_doc_id, r.target_doc_id,
                      r.source_table, r.target_table, r.source_column,
                      r.target_column, r.ordinal, r.relationship_type,
                      r.evidence_chunk_id,
                      source_doc.source_path AS source_path,
                      target_doc.source_path AS target_path
               FROM table_relationships r
               JOIN docs source_doc ON source_doc.doc_id = r.source_doc_id
               JOIN docs target_doc ON target_doc.doc_id = r.target_doc_id
               WHERE r.source_doc_id = ? OR r.target_doc_id = ?
               ORDER BY r.target_table, r.source_table, r.ordinal, r.relationship_id""",
            (seed_doc_id, seed_doc_id),
        ).fetchall()
        for row in rows:
            outbound = row["source_doc_id"] == seed_doc_id
            neighbor_doc_id = row["target_doc_id"] if outbound else row["source_doc_id"]
            if neighbor_doc_id in seen_neighbors or neighbor_doc_id in seed_document_ids:
                continue
            seen_neighbors.add(str(neighbor_doc_id))
            expanded.append(
                {
                    "relationship_id": row["relationship_id"],
                    "seed_document_id": seed_doc_id,
                    "seed_rank": seed_rank,
                    "direction": "outbound" if outbound else "inbound",
                    "related_document_id": neighbor_doc_id,
                    "related_source_path": row["target_path"] if outbound else row["source_path"],
                    "source_table": row["source_table"],
                    "target_table": row["target_table"],
                    "source_column": row["source_column"],
                    "target_column": row["target_column"],
                    "ordinal": row["ordinal"],
                    "relationship_type": row["relationship_type"],
                    "evidence_chunk_id": row["evidence_chunk_id"],
                }
            )
            if len(expanded) >= max_related_tables:
                return expanded
    return expanded


def retrieve_documentation_context(
    query: str,
    db_path: Path,
    top_k: int = 5,
    include_relationships: bool = False,
    max_related_tables: int = 5,
) -> dict:
    """Search documentation and return bounded, full chunks for answer generation."""
    if not 1 <= top_k <= MAX_RAG_TOP_K:
        raise ValueError(f"top_k must be between 1 and {MAX_RAG_TOP_K}")
    conn = open_connection(db_path)
    cur = conn.cursor()
    try:
        ranked = search_ranked_chunks(cur, query=query.strip(), top_k=top_k)
        chunks: list[dict] = []
        for rank, result in enumerate(ranked, start=1):
            chunk = fetch_chunk_by_id(cur, str(result["chunk_id"]))
            if chunk is None:
                continue
            chunk["rank"] = rank
            chunk["score"] = result.get("score")
            chunks.append(chunk)

        relationships = (
            expand_document_relationships(
                cur,
                seed_document_ids=list(dict.fromkeys(chunk["doc_id"] for chunk in chunks)),
                max_related_tables=max_related_tables,
            )
            if include_relationships
            else []
        )

        return {
            "query": query,
            "retrieval_mode": "keyword",
            "chunks": chunks,
            "relationships": relationships,
            "index_version": get_index_version(conn),
        }
    finally:
        conn.close()
