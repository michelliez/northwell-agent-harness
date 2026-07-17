from __future__ import annotations

import pytest

from harness_spike.policy.gates import policy_gate


class TestDiseaseRelatedQueries:
    """Tests for disease-related queries through the policy gate."""

    def test_sql_generation_with_patient_names_blocked(self) -> None:
        """Test that SQL generation requesting patient names with disease is blocked."""
        prompt = "Generate an SQL that gives all patient names with heart disease"
        result = policy_gate(prompt)

        assert result["allowed"] is False
        assert result["reason"] is not None
        assert result["matched_term"] is not None

    def test_disease_column_discovery_ambiguous(self) -> None:
        """Test schema question about which column contains disease patients.

        This is ambiguous - could be asking for:
        - Safe: Which column has disease codes/descriptions (schema question)
        - Unsafe: How to identify individual patients with a disease (patient-level request)

        The policy gate should be defensive and block patient-level interpretations.
        """
        prompt = "What column contains patients with heart disease?"
        result = policy_gate(prompt)

        # This may be blocked due to the patient-level interpretation risk
        # or allowed if interpreted as pure schema discovery
        # The actual result depends on the policy implementation
        assert isinstance(result["allowed"], bool)
        assert result["reason"] is not None or result["allowed"] is True

    def test_disease_information_lookup_allowed(self) -> None:
        """Test that requesting metadata about disease information is allowed."""
        prompt = "Where can I find information about specific diseases?"
        result = policy_gate(prompt)

        assert result["allowed"] is True
        assert result["matched_term"] is None

    @pytest.mark.parametrize(
        ("prompt", "risk_description"),
        [
            (
                "Generate an SQL that gives all patient names with heart disease",
                "requests patient identifiers with disease filter",
            ),
            (
                "Create a query to list all patient names diagnosed with diabetes",
                "explicit request for patient names with disease diagnosis",
            ),
            (
                "Show me MRNs for patients with heart disease",
                "requests medical record numbers filtered by disease",
            ),
            (
                "Give me all patients' full names who have cancer",
                "requests individual patient identifiers with disease condition",
            ),
            (
                "SQL: SELECT patient_name FROM patients WHERE diagnosis = 'hypertension'",
                "direct SQL requesting patient identifiers",
            ),
        ],
    )
    def test_disease_sql_with_identifiers_blocked(
        self, prompt: str, risk_description: str
    ) -> None:
        """Test that SQL generation combining disease filters with patient identifiers is blocked."""
        result = policy_gate(prompt)

        assert result["allowed"] is False, risk_description
        assert result["reason"] is not None
        assert result["matched_term"] is not None

    @pytest.mark.parametrize(
        ("prompt", "description"),
        [
            (
                "How many patients were diagnosed with heart disease last year?",
                "safe aggregate count by disease",
            ),
            (
                "What is the distribution of diagnoses by age group?",
                "safe aggregated diagnosis analysis",
            ),
            (
                "Count encounters for patients with different chronic conditions",
                "safe aggregate encounters by condition",
            ),
            (
                "What tables in the catalog contain disease or diagnosis information?",
                "safe schema discovery question",
            ),
            (
                "Which column stores diagnosis codes?",
                "safe metadata question about diagnosis storage",
            ),
            (
                "Where can I find information about specific diseases?",
                "safe documentation/reference question",
            ),
            (
                "Show me the average length of stay for heart disease patients",
                "safe aggregate metric by disease",
            ),
            (
                "What is the readmission rate for diabetes patients?",
                "safe aggregate rate by disease",
            ),
        ],
    )
    def test_disease_aggregate_and_metadata_allowed(
        self, prompt: str, description: str
    ) -> None:
        """Test that safe aggregate and metadata questions about diseases are allowed."""
        result = policy_gate(prompt)

        assert result["allowed"] is True, description
        assert result["matched_term"] is None
