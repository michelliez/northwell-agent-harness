from __future__ import annotations

from pathlib import Path

from evals.retrieval_evaluator import load_retrieval_dataset


def validate_semantic_dataset(
    queries_path: Path,
    targets_path: Path,
    chunk_targets_path: Path,
    catalog_path: Path,
) -> None:
    """Validate portable document- and chunk-level retrieval qrels."""
    load_retrieval_dataset(queries_path, targets_path, chunk_targets_path, catalog_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate the portable document-level retrieval dataset."
    )
    parser.add_argument("--queries", type=Path, default=Path("evals/retrieval_queries.jsonl"))
    parser.add_argument("--targets", type=Path, default=Path("evals/retrieval_qrels.jsonl"))
    parser.add_argument(
        "--chunk-targets",
        type=Path,
        default=Path("evals/retrieval_chunk_qrels.jsonl"),
    )
    parser.add_argument("--catalog", type=Path, default=Path("evals/retrieval_catalog.jsonl"))
    args = parser.parse_args()

    validate_semantic_dataset(args.queries, args.targets, args.chunk_targets, args.catalog)
    print("document- and chunk-level retrieval dataset validation passed")
