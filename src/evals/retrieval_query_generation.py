from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

from retrieval.indexer import discover_html_files, normalized_source_path, owned_cells, owned_rows

PROMPT_VERSION = "retrieval-description-queries-v1"
LOCAL_GENERATOR_VERSION = "deterministic-description-templates-v1"
QUERY_STYLES = ("natural_question", "keyword_search", "semantic_paraphrase")
SPLITS = ("training", "development", "evaluation")
INVALID_DESCRIPTIONS = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "not available",
    "no description",
    "unknown",
}


@dataclass(frozen=True)
class SourceDocument:
    document_key: str
    table_name: str
    description: str
    source_path: str


@dataclass(frozen=True)
class SplitDocument:
    document_key: str
    table_name: str
    description: str
    source_path: str
    split: str


@dataclass(frozen=True)
class GeneratedQuery:
    query: str
    positive_document_key: str
    source_description: str
    generation_model: str
    prompt_version: str
    query_style: str
    split: str


GenerationFunction = Callable[[str, str], str]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" :\t\r\n")


def _metadata_pairs(table: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in owned_rows(table):
        cells = owned_cells(row)
        if len(cells) < 2:
            continue
        key = _clean(cells[0].get_text(" ", strip=True)).casefold()
        value = _clean(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        if key:
            pairs[key] = value
    return pairs


def _find_value(pairs: dict[str, str], names: set[str]) -> str:
    for key, value in pairs.items():
        normalized = re.sub(r"[^a-z0-9]+", " ", key).strip()
        if normalized in names:
            return value
    return ""


def usable_description(description: str) -> bool:
    normalized = _clean(description).casefold()
    if normalized in INVALID_DESCRIPTIONS or len(normalized) < 20:
        return False
    words = re.findall(r"[a-z0-9]+", normalized)
    return len(words) >= 4 and len(set(words)) >= 3


def extract_source_document(html_path: Path, *, corpus_root: Path) -> SourceDocument | None:
    """Extract portable identity and description metadata from one Clarity HTML page."""
    soup = BeautifulSoup(
        html_path.read_text(encoding="utf-8", errors="replace"),
        "html.parser",
    )
    pairs: dict[str, str] = {}
    for table in soup.find_all("table", class_="KeyValue"):
        pairs.update(_metadata_pairs(table))

    header = soup.find("div", class_="header")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    table_name = _find_value(
        pairs,
        {"table", "table name", "tablename", "object", "object name"},
    )
    if not table_name and header is not None:
        table_name = _clean(header.get_text(" ", strip=True))
    if not table_name:
        table_name = _clean(title) or html_path.stem
    table_name = table_name.upper()

    description = _find_value(pairs, {"description", "table description"})
    if not usable_description(description):
        return None

    source_path = normalized_source_path(html_path, corpus_root)
    # The source-relative HTML path is portable and matches the identifier used
    # by the generated training examples.
    return SourceDocument(
        document_key=source_path,
        table_name=table_name,
        description=description,
        source_path=source_path,
    )


def _extract_source_document_task(args: tuple[Path, Path]) -> SourceDocument | None:
    html_path, corpus_root = args
    return extract_source_document(html_path, corpus_root=corpus_root)


def extract_source_documents(input_path: Path, *, workers: int = 1) -> list[SourceDocument]:
    html_files = discover_html_files(input_path)
    corpus_root = input_path if input_path.is_dir() else input_path.parent
    if workers < 1:
        raise ValueError("workers must be at least 1")
    worker_count = min(workers, len(html_files))
    tasks = ((path, corpus_root) for path in html_files)
    if worker_count == 1:
        extracted = map(_extract_source_document_task, tasks)
        documents = [document for document in extracted if document is not None]
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            extracted = pool.map(
                _extract_source_document_task,
                tasks,
                chunksize=32,
                buffersize=worker_count * 8,
            )
            documents = [document for document in extracted if document is not None]
    keys = [document.document_key.casefold() for document in documents]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate document_key values found in HTML corpus")
    return documents


def split_documents(
    documents: Sequence[SourceDocument],
    *,
    seed: str = "retrieval-query-split-v1",
    training_fraction: float = 0.8,
    development_fraction: float = 0.1,
) -> list[SplitDocument]:
    """Assign documents, rather than generated pairs, to deterministic partitions."""
    if not 0 < training_fraction < 1:
        raise ValueError("training_fraction must be between 0 and 1")
    if not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be between 0 and 1")
    if training_fraction + development_fraction >= 1:
        raise ValueError("training and development fractions must sum to less than 1")

    ranked = sorted(
        documents,
        key=lambda document: (
            hashlib.sha256(f"{seed}\0{document.document_key}".encode()).digest(),
            document.document_key,
        ),
    )
    count = len(ranked)
    split_counts = [
        round(count * training_fraction),
        round(count * development_fraction),
    ]
    split_counts.append(count - sum(split_counts))
    if count >= len(SPLITS):
        for split_index, split_count in enumerate(split_counts):
            if split_count:
                continue
            donor = max(range(len(split_counts)), key=split_counts.__getitem__)
            split_counts[donor] -= 1
            split_counts[split_index] += 1

    training_end = split_counts[0]
    development_end = training_end + split_counts[1]
    assignments: list[SplitDocument] = []
    for index, document in enumerate(ranked):
        split = (
            "training"
            if index < training_end
            else "development"
            if index < development_end
            else "evaluation"
        )
        assignments.append(SplitDocument(**asdict(document), split=split))
    return sorted(assignments, key=lambda document: document.document_key)


def build_prompt(document: SplitDocument) -> str:
    return f"""Generate exactly three retrieval queries answerable only from the supplied description.

Return a JSON array with one object for each style:
- natural_question: a natural user question that does not name the file or table
- keyword_search: a terse keyword-style search; it may name the table but never the filename
- semantic_paraphrase: a natural paraphrase that does not copy the description or name the file/table

Every object must have exactly these string fields:
  "style", "query", "supporting_text"
supporting_text must be an exact, non-empty substring copied from DESCRIPTION that proves the
query is answerable. Do not use outside knowledge.

TABLE: {document.table_name}
DESCRIPTION: {document.description}
"""


def _normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _nearly_copied(query: str, description: str) -> bool:
    query_text = _normalized_text(query)
    description_text = _normalized_text(description)
    if not query_text or not description_text:
        return True
    ratio = SequenceMatcher(None, query_text, description_text).ratio()
    return ratio >= 0.86


def _mentions_filename(query: str, document: SplitDocument) -> bool:
    folded = query.casefold()
    filename = Path(document.source_path).name.casefold()
    return filename in folded or ".html" in folded or ".htm" in folded


def _query_signature(query: str) -> str:
    """Return a cheap token-set signature for reordered near-duplicates."""
    return " ".join(sorted(set(_normalized_text(query).split())))


def parse_and_filter_queries(
    response: str,
    document: SplitDocument,
    *,
    seen_queries: set[str],
    seen_signatures: set[str] | None = None,
    generation_model: str,
    prompt_version: str = PROMPT_VERSION,
) -> list[GeneratedQuery]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise ValueError("generation response is not valid JSON") from error
    if not isinstance(payload, list):
        raise ValueError("generation response must be a JSON array")

    accepted: list[GeneratedQuery] = []
    seen_styles: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        style = str(item.get("style", "")).strip()
        query = _clean(str(item.get("query", "")))
        supporting_text = _clean(str(item.get("supporting_text", "")))
        normalized_query = _normalized_text(query)
        signature = _query_signature(query)

        invalid = (
            style not in QUERY_STYLES
            or style in seen_styles
            or len(query) < 8
            or not supporting_text
            or supporting_text.casefold() not in document.description.casefold()
            or _mentions_filename(query, document)
            or _nearly_copied(query, document.description)
            or normalized_query in seen_queries
            or (seen_signatures is not None and signature in seen_signatures)
        )
        if invalid:
            continue

        accepted.append(
            GeneratedQuery(
                query=query,
                positive_document_key=document.document_key,
                source_description=document.description,
                generation_model=generation_model,
                prompt_version=prompt_version,
                query_style=style,
                split=document.split,
            )
        )
        seen_styles.add(style)
        seen_queries.add(normalized_query)
        if seen_signatures is not None:
            seen_signatures.add(signature)
    return accepted


def _description_topic(description: str) -> str:
    topic = _clean(description).rstrip(".")
    topic = re.sub(
        (
            r"^(?:(?:this|the)\s+)?(?:table\s+)?(?:(?:is|are)\s+)?"
            r"(?:used\s+to\s+|stores?|contains?|includes?|provides?|lists?|tracks?|"
            r"extracts?|identifies?|holds?|records?|describes?|captures?|documents?)\s+"
        ),
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.split(r"(?<=[.!?])\s+", topic, maxsplit=1)[0].rstrip(".")
    return topic or _clean(description).rstrip(".")


def _keywords(description: str, *, limit: int = 6) -> list[str]:
    stop_words = {
        "about",
        "all",
        "also",
        "and",
        "are",
        "between",
        "can",
        "contains",
        "data",
        "during",
        "for",
        "from",
        "has",
        "have",
        "includes",
        "information",
        "into",
        "its",
        "not",
        "note",
        "of",
        "or",
        "records",
        "specified",
        "stores",
        "table",
        "that",
        "the",
        "their",
        "this",
        "to",
        "used",
        "with",
    }
    result: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", description):
        folded = token.casefold()
        if len(folded) < 3 or folded in stop_words or folded in result:
            continue
        result.append(folded)
        if len(result) == limit:
            break
    return result


def _semantic_terms(description: str) -> str:
    synonyms = {
        "activities": "actions",
        "appointment": "scheduled visit",
        "appointments": "scheduled visits",
        "community": "cross-instance identifiers",
        "date": "timing",
        "dates": "timing",
        "encounter": "care interaction",
        "encounters": "care interactions",
        "extracts": "retrieval",
        "form": "documents",
        "history": "past activity",
        "hyperspace": "Hyperspace access",
        "internal": "system-specific",
        "list": "inventory",
        "mapping": "linkage",
        "medication": "drug",
        "medications": "drugs",
        "patient": "care recipient",
        "patients": "people receiving care",
        "paper": "printed documents",
        "record": "entry",
        "records": "entries",
        "status": "state",
        "updated": "changes",
    }
    terms: list[str] = []
    for keyword in _keywords(description, limit=8):
        term = synonyms.get(keyword, keyword)
        if term in {"access", "cid", "ids"} and any(
            "identifiers" in existing or existing == "Hyperspace access" for existing in terms
        ):
            continue
        if term == "documents" and "printed documents" in terms:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) == 5:
            break
    if not terms:
        return "the documented subject"
    if len(terms) == 1:
        return terms[0]
    return f"{', '.join(terms[:-1])}, and {terms[-1]}"


def deterministic_generation(prompt: str, model: str) -> str:
    """Generate grounded query candidates locally from the prompt fields."""
    table_match = re.search(r"^TABLE:\s*(.+)$", prompt, flags=re.MULTILINE)
    description_match = re.search(r"^DESCRIPTION:\s*(.+)$", prompt, flags=re.MULTILINE)
    if table_match is None or description_match is None:
        raise ValueError("prompt is missing TABLE or DESCRIPTION")
    table_name = _clean(table_match.group(1))
    description = _clean(description_match.group(1))
    topic = _description_topic(description)
    keyword_text = " ".join(_keywords(description))
    semantic_terms = _semantic_terms(description)
    return json.dumps(
        [
            {
                "style": "natural_question",
                "query": f"Where can I find information about {topic}?",
                "supporting_text": description,
            },
            {
                "style": "keyword_search",
                "query": (f"{table_name} schema catalog purpose definition {keyword_text}").strip(),
                "supporting_text": description,
            },
            {
                "style": "semantic_paraphrase",
                "query": f"Which data source is relevant to {semantic_terms}?",
                "supporting_text": description,
            },
        ],
        ensure_ascii=False,
    )


def generate_query_records(
    documents: Sequence[SplitDocument],
    generate: GenerationFunction,
    *,
    generation_model: str,
    prompt_version: str = PROMPT_VERSION,
) -> list[GeneratedQuery]:
    seen_queries: set[str] = set()
    seen_signatures: set[str] = set()
    records: list[GeneratedQuery] = []
    for document in documents:
        response = generate(build_prompt(document), generation_model)
        records.extend(
            parse_and_filter_queries(
                response,
                document,
                seen_queries=seen_queries,
                seen_signatures=seen_signatures,
                generation_model=generation_model,
                prompt_version=prompt_version,
            )
        )
    return records


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_dataset(
    output_dir: Path,
    documents: Sequence[SplitDocument],
    records: Sequence[GeneratedQuery],
) -> None:
    _write_jsonl(output_dir / "documents.jsonl", (asdict(document) for document in documents))
    for split in SPLITS:
        _write_jsonl(
            output_dir / f"{split}_queries.jsonl",
            (asdict(record) for record in records if record.split == split),
        )


def load_split_documents(path: Path) -> list[SplitDocument]:
    documents: list[SplitDocument] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                document = SplitDocument(**data)
            except (json.JSONDecodeError, TypeError) as error:
                raise ValueError(f"invalid document manifest row {line_number}") from error
            if document.split not in SPLITS:
                raise ValueError(
                    f"invalid split {document.split!r} on document manifest row {line_number}"
                )
            documents.append(document)
    return documents


def anthropic_generation(prompt: str, model: str) -> str:
    import anthropic

    message = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=800,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    return "\n".join(text_blocks)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract usable HTML table descriptions and generate split-safe queries."
    )
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/retrieval/generated"))
    parser.add_argument(
        "--generator",
        choices=("anthropic", "local"),
        default="anthropic",
        help="Use Anthropic generation or deterministic local templates.",
    )
    parser.add_argument("--model")
    parser.add_argument("--seed", default="retrieval-query-split-v1")
    parser.add_argument(
        "--workers",
        type=int,
        default=min(os.cpu_count() or 1, 8),
        help="Parallel HTML parser processes.",
    )
    parser.add_argument(
        "--reuse-documents",
        action="store_true",
        help="Reuse OUTPUT_DIR/documents.jsonl instead of reparsing and resplitting HTML.",
    )
    args = parser.parse_args()

    manifest_path = args.output_dir / "documents.jsonl"
    if args.reuse_documents:
        split_documents_first = load_split_documents(manifest_path)
    else:
        documents = extract_source_documents(args.input_path, workers=args.workers)
        split_documents_first = split_documents(documents, seed=args.seed)
    if args.generator == "anthropic" and not args.model:
        parser.error("--model is required when --generator anthropic")
    generation_function = (
        anthropic_generation if args.generator == "anthropic" else deterministic_generation
    )
    generation_model = args.model or LOCAL_GENERATOR_VERSION
    records = generate_query_records(
        split_documents_first,
        generation_function,
        generation_model=generation_model,
    )
    write_dataset(args.output_dir, split_documents_first, records)
    print(
        f"Wrote {len(split_documents_first)} usable documents and {len(records)} queries "
        f"to {args.output_dir}"
    )


if __name__ == "__main__":
    main()
