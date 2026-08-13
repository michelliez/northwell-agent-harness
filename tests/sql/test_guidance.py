from __future__ import annotations

from sql.guidance import describe_violations
from sql.models import SqlViolation


def _v(code: str, **evidence: object) -> SqlViolation:
    return SqlViolation(code=code, message=code, evidence=dict(evidence))


def test_actionable_codes_produce_specific_guidance() -> None:
    text = describe_violations([_v("unsafe_filter"), _v("time_bucket_not_temporal")])

    assert "safe" in text
    assert "DATE" in text


def test_duplicate_codes_do_not_repeat_guidance() -> None:
    single = describe_violations([_v("unsafe_filter")])
    doubled = describe_violations([_v("unsafe_filter"), _v("unsafe_filter")])

    assert doubled == single


def test_unknown_codes_fall_back_to_generic_line() -> None:
    assert describe_violations([_v("internal_only_code")]) == "Please rephrase your question."
    assert describe_violations([]) == "Please rephrase your question."


def test_unknown_safety_column_names_the_column() -> None:
    text = describe_violations([_v("unknown_safety_column", column="PAT_ENC.MY_COL")])

    assert "PAT_ENC.MY_COL" in text
    assert "safety" in text.lower()


def test_sensitive_column_reference_names_the_column() -> None:
    text = describe_violations([_v("sensitive_column_reference", column="PATIENTS.ZIP3")])

    assert "PATIENTS.ZIP3" in text
    assert "sensitive" in text.lower()


def test_identifier_column_disallowed_context_names_the_column() -> None:
    text = describe_violations(
        [_v("identifier_column_disallowed_context", column="PAT_ENC.PAT_ID")]
    )

    assert "PAT_ENC.PAT_ID" in text
    assert "identifier" in text.lower()


def test_missing_column_evidence_falls_back_to_unknown() -> None:
    # no 'column' key in evidence — format slot defaults gracefully
    text = describe_violations([_v("unknown_safety_column")])

    assert "unknown" in text


def test_output_shape_mismatch_shows_declared_and_required_sets() -> None:
    text = describe_violations(
        [
            _v(
                "output_shape_mismatch",
                value={
                    "declared": ["admission_date", "encounter_count"],
                    "required": ["admission_date", "encounter_count", "hosp_admsn_time"],
                },
            )
        ]
    )

    assert "declared: admission_date, encounter_count" in text
    assert "required: admission_date, encounter_count, hosp_admsn_time" in text


def test_evidence_value_detail_is_appended_inside_the_sentence() -> None:
    text = describe_violations([_v("table_out_of_scope", value=["ZZ_FAKE_TABLE"])])

    assert text.endswith("(ZZ_FAKE_TABLE).")


def test_violations_without_value_evidence_keep_the_plain_sentence() -> None:
    assert describe_violations([_v("unsafe_join")]) == (
        "Tables can only be joined on their documented identifier columns."
    )
