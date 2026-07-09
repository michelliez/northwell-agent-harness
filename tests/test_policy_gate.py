from harness_spike.gates import policy_gate


def test_policy_gate_allows_aggregate_question() -> None:
    result = policy_gate("How many encounters happened last month?")

    assert result == {
        "allowed": True,
        "reason": None,
        "matched_term": None,
    }


def test_policy_gate_blocks_patient_identifiers() -> None:
    result = policy_gate("Show me patient name and MRN for recent visits")

    assert result["allowed"] is False
    assert result["reason"] == "Requests patient-identifying information"
    assert result["matched_term"] == "patient name"


def test_policy_gate_blocks_destructive_actions() -> None:
    result = policy_gate("Drop the encounters table")

    assert result == {
        "allowed": False,
        "reason": "Requests a destructive database action",
        "matched_term": "drop",
    }
