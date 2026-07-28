from __future__ import annotations

import json
from pathlib import Path

from evals.retrieval_query_generation import (
    SourceDocument,
    deterministic_generation,
    extract_source_documents,
    generate_query_records,
    split_documents,
    write_dataset,
)


def _write_doc(path: Path, table_name: str, description: str) -> None:
    path.write_text(
        f"""
        <html><head><title>{table_name}</title></head><body>
        <div class="header">{table_name}</div>
        <div id="oContent"><table class="KeyValue">
          <tr><td class="T1Head">Table Name</td><td>{table_name}</td></tr>
          <tr><td class="T1Head">Description:</td><td>{description}</td></tr>
        </table></div>
        </body></html>
        """,
        encoding="utf-8",
    )


def test_extracts_only_html_documents_with_usable_descriptions(tmp_path: Path) -> None:
    _write_doc(
        tmp_path / "CUSTOMER_ACCOUNT.html",
        "CUSTOMER_ACCOUNT",
        "Stores customer account activation dates and current account state.",
    )
    _write_doc(tmp_path / "EMPTY.html", "EMPTY", "N/A")

    documents = extract_source_documents(tmp_path)

    assert documents == [
        SourceDocument(
            document_key="CUSTOMER_ACCOUNT.html",
            table_name="CUSTOMER_ACCOUNT",
            description="Stores customer account activation dates and current account state.",
            source_path="CUSTOMER_ACCOUNT.html",
        )
    ]


def test_documents_are_split_before_generation_and_do_not_cross_splits() -> None:
    documents = [
        SourceDocument(
            document_key=f"TABLE_{index}.html",
            table_name=f"TABLE_{index}",
            description=f"Stores activation information for account group number {index}.",
            source_path=f"TABLE_{index}.html",
        )
        for index in range(20)
    ]

    first = split_documents(documents, seed="fixed")
    second = split_documents(list(reversed(documents)), seed="fixed")

    assert first == second
    assert {document.split for document in first} == {
        "training",
        "development",
        "evaluation",
    }
    assert len({document.document_key for document in first}) == len(first)


def test_small_corpus_still_populates_all_three_splits() -> None:
    documents = [
        SourceDocument(
            document_key=f"TABLE_{index}.html",
            table_name=f"TABLE_{index}",
            description=f"Stores activation information for account group number {index}.",
            source_path=f"TABLE_{index}.html",
        )
        for index in range(3)
    ]

    assigned = split_documents(documents)

    assert {document.split for document in assigned} == set(
        ("training", "development", "evaluation")
    )


def test_generation_filters_unsupported_copied_filename_and_duplicate_queries(
    tmp_path: Path,
) -> None:
    [document] = split_documents(
        [
            SourceDocument(
                document_key="CUSTOMER_ACCOUNT.html",
                table_name="CUSTOMER_ACCOUNT",
                description="Stores customer account activation dates and current account state.",
                source_path="CUSTOMER_ACCOUNT.html",
            )
        ],
        training_fraction=0.8,
        development_fraction=0.1,
    )

    response = json.dumps(
        [
            {
                "style": "natural_question",
                "query": "Where is a customer's activation date stored?",
                "supporting_text": "customer account activation dates",
            },
            {
                "style": "keyword_search",
                "query": "CUSTOMER_ACCOUNT activation date",
                "supporting_text": "activation dates",
            },
            {
                "style": "semantic_paraphrase",
                "query": "Which record identifies when an account first became active?",
                "supporting_text": "account activation dates",
            },
            {
                "style": "natural_question",
                "query": "What is in CUSTOMER_ACCOUNT.html?",
                "supporting_text": "account activation dates",
            },
            {
                "style": "semantic_paraphrase",
                "query": "Stores customer account activation dates and current account state",
                "supporting_text": "current account state",
            },
            {
                "style": "keyword_search",
                "query": "CUSTOMER_ACCOUNT activation date",
                "supporting_text": "not present in the source",
            },
        ]
    )
    records = generate_query_records(
        [document],
        lambda _prompt, _model: response,
        generation_model="test-model",
    )
    write_dataset(tmp_path, [document], records)

    assert [record.query_style for record in records] == [
        "natural_question",
        "keyword_search",
        "semantic_paraphrase",
    ]
    assert all(record.positive_document_key == "CUSTOMER_ACCOUNT.html" for record in records)
    output_path = tmp_path / f"{document.split}_queries.jsonl"
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert rows[0]["generation_model"] == "test-model"
    assert rows[0]["prompt_version"] == "retrieval-description-queries-v1"


def test_deterministic_generation_produces_three_grounded_styles() -> None:
    [document] = split_documents(
        [
            SourceDocument(
                document_key="PATIENT_VISITS.html",
                table_name="PATIENT_VISITS",
                description="Stores patient appointment dates and visit status history.",
                source_path="PATIENT_VISITS.html",
            )
        ]
    )

    records = generate_query_records(
        [document],
        deterministic_generation,
        generation_model="deterministic-description-templates-v1",
    )

    assert {record.query_style for record in records} == {
        "natural_question",
        "keyword_search",
        "semantic_paraphrase",
    }
    assert all(".html" not in record.query.casefold() for record in records)
