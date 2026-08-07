from __future__ import annotations

from sql.guidance import describe_violations


def test_actionable_codes_produce_specific_guidance() -> None:
    text = describe_violations(["unsafe_filter", "time_bucket_not_temporal"])

    assert "safe" in text
    assert "DATE" in text


def test_duplicate_codes_do_not_repeat_guidance() -> None:
    single = describe_violations(["unsafe_filter"])
    doubled = describe_violations(["unsafe_filter", "unsafe_filter"])

    assert doubled == single


def test_unknown_codes_fall_back_to_generic_line() -> None:
    assert describe_violations(["internal_only_code"]) == "Please rephrase your question."
    assert describe_violations([]) == "Please rephrase your question."
