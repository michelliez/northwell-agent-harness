"""Command-line entry point for local Qwen3 synthetic-query generation.

The generation pipeline itself is shared with the Claude path: this module only
chooses the provider. ``query_generation.generate_queries`` still owns chunk
selection, identity, duplicate accounting, split provenance, and the report, so
locally generated queries are directly comparable with API-generated ones and
``GeneratedQueryRecord.generator_model`` records which produced each.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from retrieval.genq.query_generation import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_QUERY_TOKENS,
    DEFAULT_MIN_PASSAGE_CHARS,
    DEFAULT_SEED,
    DEFAULT_TOP_P,
    GenerationConfig,
    generate_queries,
)
from retrieval.genq.qwen_query_generator import DEFAULT_QWEN_MODEL, QwenQueryGenerator

DEFAULT_LOCAL_BATCH_SIZE = 16
DEFAULT_LOCAL_QUERIES_PER_CHUNK = 2
LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Run local Qwen3 query generation from the command line."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic queries locally with a Qwen3 instruct model."
    )
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_QWEN_MODEL, help="Hugging Face model ID")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_LOCAL_BATCH_SIZE,
        help="Passages per GPU forward pass. Raise until VRAM is saturated.",
    )
    parser.add_argument("--queries-per-chunk", type=int, default=DEFAULT_LOCAL_QUERIES_PER_CHUNK)
    parser.add_argument("--max-input-tokens", type=int, default=DEFAULT_MAX_INPUT_TOKENS)
    parser.add_argument("--max-query-tokens", type=int, default=DEFAULT_MAX_QUERY_TOKENS)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--min-passage-chars", type=int, default=DEFAULT_MIN_PASSAGE_CHARS)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--device", default="auto", choices=("auto", "cuda", "cpu"), help="Inference device"
    )
    parser.add_argument(
        "--no-4bit",
        action="store_true",
        default=False,
        help="Load in bf16 instead of 4-bit NF4 (needs roughly 8 GB for a 4B model)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Re-sample attempts for a passage whose reply is not parseable JSON",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level), format="%(levelname)s %(name)s: %(message)s"
    )
    load_dotenv()

    try:
        generator = QwenQueryGenerator(
            args.model,
            device=args.device,
            load_in_4bit=not args.no_4bit,
            batch_size=args.batch_size,
            max_retries=args.max_retries,
        )
        report = generate_queries(
            GenerationConfig(
                input_path=args.input_path,
                output_path=args.output,
                report_path=args.report,
                model_name=args.model,
                seed=args.seed,
                # The shared pipeline hands the generator one slice at a time; the
                # generator batches internally, so this only sets log granularity.
                batch_size=args.batch_size,
                queries_per_chunk=args.queries_per_chunk,
                max_input_tokens=args.max_input_tokens,
                max_query_tokens=args.max_query_tokens,
                top_p=args.top_p,
                min_passage_chars=args.min_passage_chars,
                limit=args.limit,
            ),
            generator=generator,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Generated {report.generated_query_count:,} raw queries from "
        f"{report.selected_chunk_count:,} chunks on {report.device}; "
        f"output_hash={report.output_query_hash}"
    )


if __name__ == "__main__":
    main()
