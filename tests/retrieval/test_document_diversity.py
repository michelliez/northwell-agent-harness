"""One verbose document must not consume the whole retrieval budget.

Results are chunk-level. Before the cap, a probe for a patient's primary care
provider returned five chunks of the same table document out of eight, and the
tables that actually answered the question never made it into the budget. The
cap takes the best few chunks per document first and backfills with what it
displaced, so recall is unchanged and only ordering moves.
"""

from __future__ import annotations

import pytest

from retrieval.search import (
    MAX_CHUNKS_PER_DOCUMENT,
    apply_document_diversity,
    document_key,
)


def _chunk(doc: str, n: int) -> dict:
    return {"chunk_id": f"{doc}-{n}", "source_path": f"{doc}.html", "title": f"{doc} - Dictionary"}


def _ranked(*spec: tuple[str, int]) -> list[dict]:
    """Build a ranked list from (document, count) pairs, in rank order."""
    return [_chunk(doc, i) for doc, count in spec for i in range(count)]


def _docs(results: list[dict]) -> list[str]:
    return [document_key(r) for r in results]


# --- the cap binds ------------------------------------------------------------


def test_one_document_no_longer_takes_every_slot() -> None:
    ranked = _ranked(("DM_CANCER_PATIENT_HX", 8), ("PAT_ENC", 1), ("PATIENT", 1))
    kept = apply_document_diversity(ranked, top_k=5, max_per_document=3)

    assert len(kept) == 5
    assert _docs(kept).count("DM_CANCER_PATIENT_HX") == 3
    assert "PAT_ENC" in _docs(kept), "the tables that answer the question were squeezed out"
    assert "PATIENT" in _docs(kept)


def test_default_cap_is_applied_when_not_specified() -> None:
    ranked = _ranked(("ONE", 10), ("TWO", 5))
    kept = apply_document_diversity(ranked, top_k=MAX_CHUNKS_PER_DOCUMENT + 1)
    assert _docs(kept).count("ONE") == MAX_CHUNKS_PER_DOCUMENT


# --- but never at the cost of filling the budget ------------------------------


def test_backfill_fills_the_budget_when_one_document_is_the_answer() -> None:
    """A query matching a single document must still return that document."""
    ranked = _ranked(("ABN_ORDERS", 8))
    kept = apply_document_diversity(ranked, top_k=8, max_per_document=3)

    assert len(kept) == 8, "diversity must not shrink the result set"
    assert set(_docs(kept)) == {"ABN_ORDERS"}


def test_backfill_preserves_rank_order_within_each_pass() -> None:
    ranked = _ranked(("A", 5), ("B", 1))
    kept = apply_document_diversity(ranked, top_k=6, max_per_document=2)

    assert [r["chunk_id"] for r in kept] == ["A-0", "A-1", "B-0", "A-2", "A-3", "A-4"]


def test_never_returns_more_than_top_k() -> None:
    ranked = _ranked(("A", 20), ("B", 20), ("C", 20))
    assert len(apply_document_diversity(ranked, top_k=7, max_per_document=3)) == 7


# --- an explicitly named document is not diversified away ---------------------


def test_named_document_is_exempt_from_the_cap() -> None:
    """Asking about ABN_ORDERS should return ABN_ORDERS, not a survey."""
    ranked = _ranked(("ABN_ORDERS", 8), ("UTL_VLD_HNO", 4))
    kept = apply_document_diversity(
        ranked, top_k=10, max_per_document=3, exempt_document="ABN_ORDERS"
    )
    assert _docs(kept).count("ABN_ORDERS") == 8


def test_exemption_matches_the_document_key_not_a_substring() -> None:
    """`PAT_ENC` must not exempt `PAT_ENC_HSP`.

    `top_k` is sized to the first pass exactly, so backfill cannot refill the
    capped document and hide a substring match.
    """
    ranked = _ranked(("PAT_ENC_HSP", 8), ("PAT_ENC", 4))
    kept = apply_document_diversity(ranked, top_k=6, max_per_document=2, exempt_document="PAT_ENC")
    assert _docs(kept).count("PAT_ENC_HSP") == 2, "the capped document was treated as exempt"
    assert _docs(kept).count("PAT_ENC") == 4


@pytest.mark.parametrize("hint", ["ABN_ORDERS", "abn_orders", "ABN_ORDERS.html"])
def test_exemption_normalizes_the_hint(hint: str) -> None:
    ranked = _ranked(("ABN_ORDERS", 6))
    kept = apply_document_diversity(ranked, top_k=6, max_per_document=1, exempt_document=hint)
    assert len(kept) == 6


# --- disabling -----------------------------------------------------------------


@pytest.mark.parametrize("cap", [0, -1])
def test_a_non_positive_cap_disables_diversity(cap: int) -> None:
    ranked = _ranked(("A", 10))
    kept = apply_document_diversity(ranked, top_k=5, max_per_document=cap)
    assert [r["chunk_id"] for r in kept] == ["A-0", "A-1", "A-2", "A-3", "A-4"]


# --- grouping key --------------------------------------------------------------


def test_document_key_groups_chunks_from_the_same_file() -> None:
    assert document_key(_chunk("PAT_ENC", 1)) == document_key(_chunk("PAT_ENC", 2))
    assert document_key(_chunk("PAT_ENC", 1)) != document_key(_chunk("PAT_ENC_HSP", 1))


def test_document_key_falls_back_to_title_when_path_is_missing() -> None:
    assert document_key({"title": "PAT_ENC - Dictionary"}) != ""


def test_chunks_with_no_identity_do_not_all_collapse_into_one_document() -> None:
    """An empty key would otherwise let three unrelated chunks cap each other."""
    ranked = [{"chunk_id": "x"}, {"chunk_id": "y"}, {"chunk_id": "z"}]
    kept = apply_document_diversity(ranked, top_k=3, max_per_document=1)
    assert len(kept) == 3, "unidentifiable chunks must still fill the budget"
