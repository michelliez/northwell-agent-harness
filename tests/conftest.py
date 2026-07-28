"""Shared test fixtures for the simplified pipeline test suite."""

from __future__ import annotations

import pytest

from sql.models import SchemaColumn, SchemaSnapshot, SchemaTable


@pytest.fixture
def standard_snapshot() -> SchemaSnapshot:
    """SchemaSnapshot matching the former synthetic catalog fixture.

    Provides the same three tables (encounters, patients, appointments)
    with the same safety labels that the old data_catalog.py used, so that
    SQL validation tests can use explicit schema evidence instead of a global.
    """
    return SchemaSnapshot(
        tables=[
            SchemaTable(
                name="encounters",
                description="One row per patient visit or encounter.",
                columns=[
                    SchemaColumn(
                        name="encounter_id",
                        data_type="STRING",
                        safety="identifier",
                    ),
                    SchemaColumn(
                        name="patient_id",
                        data_type="STRING",
                        safety="identifier",
                    ),
                    SchemaColumn(
                        name="encounter_date",
                        data_type="DATE",
                        safety="safe_aggregate",
                    ),
                    SchemaColumn(
                        name="department",
                        data_type="STRING",
                        safety="safe_aggregate",
                    ),
                ],
                source_chunk_ids=["fixture-encounters"],
            ),
            SchemaTable(
                name="patients",
                description="Synthetic patient fixture.",
                columns=[
                    SchemaColumn(
                        name="patient_id",
                        data_type="STRING",
                        safety="identifier",
                    ),
                    SchemaColumn(
                        name="birth_year",
                        data_type="INTEGER",
                        safety="safe_aggregate",
                    ),
                    SchemaColumn(
                        name="zip3",
                        data_type="STRING",
                        safety="sensitive",
                    ),
                ],
                source_chunk_ids=["fixture-patients"],
            ),
            SchemaTable(
                name="appointments",
                description="Scheduled appointment records.",
                columns=[
                    SchemaColumn(
                        name="appointment_id",
                        data_type="STRING",
                        safety="identifier",
                    ),
                    SchemaColumn(
                        name="appointment_date",
                        data_type="DATE",
                        safety="safe_aggregate",
                    ),
                    SchemaColumn(
                        name="status",
                        data_type="STRING",
                        safety="safe_aggregate",
                    ),
                ],
                source_chunk_ids=["fixture-appointments"],
            ),
        ],
        index_version="fixture-v1",
        derived_from_chunks=["fixture-encounters", "fixture-patients", "fixture-appointments"],
    )
