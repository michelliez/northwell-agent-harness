from harness_spike.mcp_servers.data_catalog import get_table_info, search_docs


def test_search_docs_finds_matching_dummy_docs() -> None:
    results = search_docs("diagnosis")

    assert results == [
        {
            "name": "diagnoses",
            "description": (
                "Contains diagnosis codes and diagnosis descriptions for dummy "
                "clinical metadata exploration."
            ),
        }
    ]


def test_get_table_info_returns_known_table_metadata() -> None:
    result = get_table_info("encounters")

    assert result == {
        "table": "encounters",
        "primary_key": "encounter_id",
        "description": (
            "Contains admission, discharge, department, and encounter date "
            "information."
        ),
    }


def test_get_table_info_returns_error_for_unknown_table() -> None:
    result = get_table_info("patients")

    assert result == {
        "error": "Unknown table: patients",
    }
