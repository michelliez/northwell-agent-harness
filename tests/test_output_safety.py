"""Unit tests for output safety classifier node."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_host.nodes.output_safety_nodes import (
    _RawOutputSafetyAssessment,
    enforce_output_safety_contract,
)


class TestOutputSafetyContract:
    """Tests for output safety assessment contract enforcement."""

    def test_no_issues_defaults_to_allow(self):
        """Answer with no issues should be allowed."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=False,
            has_accuracy_concerns=False,
            confidence=0.95,
            risk_flags=[],
            recommended_action="allow",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["recommended_action"] == "allow"
        assert result["risk_flags"] == []

    def test_low_confidence_defaults_to_flag(self):
        """Low confidence assessment should be flagged regardless of content."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=False,
            has_accuracy_concerns=False,
            confidence=0.50,  # Below 0.70 threshold
            risk_flags=["unclear"],
            recommended_action="allow",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["recommended_action"] == "flag"
        assert "low_confidence" in result["risk_flags"]

    def test_policy_violations_flagged(self):
        """Assessment with policy violations should be flagged."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=True,
            has_accuracy_concerns=False,
            confidence=0.85,
            risk_flags=["appears_to_disclose_pii"],
            recommended_action="flag",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["has_policy_violations"] is True
        assert result["recommended_action"] == "flag"
        assert "appears_to_disclose_pii" in result["risk_flags"]

    def test_accuracy_concerns_flagged(self):
        """Assessment with accuracy concerns should be flagged."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=False,
            has_accuracy_concerns=True,
            confidence=0.80,
            risk_flags=["contradicts_chunks", "unsupported_claim"],
            recommended_action="flag",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["has_accuracy_concerns"] is True
        assert result["recommended_action"] == "flag"
        assert "contradicts_chunks" in result["risk_flags"]
        assert "unsupported_claim" in result["risk_flags"]

    def test_block_recommendation_takes_precedence(self):
        """Block recommendation should override other logic."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=False,
            has_accuracy_concerns=False,
            confidence=0.95,
            risk_flags=[],
            recommended_action="block",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["recommended_action"] == "block"

    def test_both_violations_and_concerns(self):
        """Answer with both violations and concerns should be flagged."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=True,
            has_accuracy_concerns=True,
            confidence=0.88,
            risk_flags=["appears_to_disclose_pii", "unsupported_claim"],
            recommended_action="flag",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["has_policy_violations"] is True
        assert result["has_accuracy_concerns"] is True
        assert result["recommended_action"] == "flag"

    def test_risk_flags_sorted(self):
        """Risk flags should be returned in sorted order."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=True,
            has_accuracy_concerns=False,
            confidence=0.75,
            risk_flags=["zebra_flag", "apple_flag", "banana_flag"],
            recommended_action="flag",
        )
        result = enforce_output_safety_contract(assessment)
        assert result["risk_flags"] == ["apple_flag", "banana_flag", "zebra_flag"]

    def test_custom_confidence_threshold(self):
        """Custom confidence thresholds should be respected."""
        assessment = _RawOutputSafetyAssessment(
            has_policy_violations=False,
            has_accuracy_concerns=False,
            confidence=0.75,
            risk_flags=[],
            recommended_action="allow",
        )
        # Default threshold is 0.70, so 0.75 should pass
        result_default = enforce_output_safety_contract(assessment)
        assert result_default["recommended_action"] == "allow"

        # With higher threshold, same assessment should be flagged
        result_higher = enforce_output_safety_contract(assessment, min_confidence=0.80)
        assert result_higher["recommended_action"] == "flag"

    def test_pydantic_validation_fails_on_extra_fields(self):
        """Pydantic should reject extra fields."""
        with pytest.raises(ValidationError):
            _RawOutputSafetyAssessment(
                has_policy_violations=False,
                has_accuracy_concerns=False,
                confidence=0.95,
                risk_flags=[],
                recommended_action="allow",
                extra_field="should_fail",  # type: ignore
            )

    def test_pydantic_validates_confidence_bounds(self):
        """Pydantic should enforce confidence bounds."""
        with pytest.raises(ValidationError):
            _RawOutputSafetyAssessment(
                has_policy_violations=False,
                has_accuracy_concerns=False,
                confidence=1.5,  # Above max of 1.0
                risk_flags=[],
                recommended_action="allow",
            )

    def test_pydantic_validates_action_enum(self):
        """Pydantic should enforce allowed action values."""
        with pytest.raises(ValidationError):
            _RawOutputSafetyAssessment(
                has_policy_violations=False,
                has_accuracy_concerns=False,
                confidence=0.95,
                risk_flags=[],
                recommended_action="invalid_action",  # type: ignore
            )
