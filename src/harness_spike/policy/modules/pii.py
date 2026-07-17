from __future__ import annotations

from harness_spike.policy.normalize import (
    contains_phrase,
    is_schema_metadata_request,
    matches_blocked_term,
    normalize_leetspeak,
    normalize_prompt,
)
from harness_spike.policy.result import PolicyGateResult, blocked


SMALL_CELL_RISK_TERMS: dict[str, str] = {
    "smallest": "Potential small-cell disclosure risk",
    "only one": "Requests small-cell output",
    "unique encounter": "Requests singling out an individual record",
    "unique case": "Requests singling out an individual record",
    "exactly one": "Requests small-cell output",
    "birth year": "Combines quasi-identifiers",
    "zip3": "Combines quasi-identifiers",
    "rare diagnosis": "Potential re-identification through rare conditions",
    "rare diagnoses": "Potential re-identification through rare conditions",
    "rare disease": "Potential re-identification through rare conditions",
    "rare diseases": "Potential re-identification through rare conditions",
    "exact timestamp": "Requests overly granular aggregation",
    "unusual cases": "Requests potentially identifying rare cases",
    "most unique": "Requests singling out an individual record",
}

KNOWN_PERSON_LOOKUP_TERMS: dict[str, str] = {
    "celebrity": "Requests confirmation of an individual's health-system presence",
    "coworker": "Requests sensitive information about a known person",
    "was anyone from": "Potentially identifying query through location and time",
}

IDENTIFIER_TERMS: dict[str, str] = {
    "patient name": "Requests patient-identifying information",
    "patient names": "Requests patient-identifying information",
    "pt name": "Requests patient-identifying information",
    "pt names": "Requests patient-identifying information",
    "full name": "Requests patient-identifying information",
    "their names": "Requests patient-identifying information",
    "ssn": "Requests patient-identifying information",
    "phi": "Requests patient-identifying information",
    "pii": "Requests patient-identifying information",
    "identifier": "Requests patient-identifying information",
    "identifiers": "Requests patient-identifying information",
    "contact info": "Requests patient-identifying information",
    "member id": "Requests member identifiers",
    "account number": "Requests account identifiers",
    "mrn": "Requests medical record numbers",
    "mrns": "Requests medical record numbers",
    "medical record number": "Requests medical record numbers",
    "medical record numbers": "Requests medical record numbers",
    "member ids": "Requests member identifiers",
    "account numbers": "Requests account identifiers",
    "subscriber id": "Requests member identifiers",
    "patient id": "Requests patient identifiers",
    "patient ids": "Requests patient identifiers",
    "dob": "Requests patient-identifying information",
    "dobs": "Requests patient-identifying information",
    "date of birth": "Requests patient-identifying information",
    "birthdate": "Requests patient-identifying information",
    "birthdates": "Requests patient-identifying information",
    "birth date": "Requests patient-identifying information",
    "birth dates": "Requests patient-identifying information",
    "address": "Requests patient-identifying information",
    "addresses": "Requests patient-identifying information",
    "phone": "Requests patient contact information",
    "phone number": "Requests patient-identifying information",
    "phone numbers": "Requests patient-identifying information",
    "telephone": "Requests patient-identifying information",
    "telephone number": "Requests patient-identifying information",
    "email": "Requests patient-identifying information",
    "e mail": "Requests patient contact information",
    "email address": "Requests patient contact information",
    "emails": "Requests patient-identifying information",
    "home address": "Requests patient address information",
    "family members": "Requests patient-identifying information",
    "relatives": "Requests patient-identifying information",
}

INDIVIDUAL_REQUEST_TERMS: dict[str, str] = {
    "which patient": "Asks for individual patient information",
    "which patients": "Asks for individual patient information",
    "who was": "Asks for individual patient information",
    "who wsa": "Asks for individual patient information",
    "who had": "Asks for individual patient information",
    "find the patient": "Asks for individual patient information",
    "top patient": "Asks for individual patient information",
    "oldest patient": "Asks for individual patient information",
    "youngest patient": "Asks for individual patient information",
}

INDIRECT_IDENTITY_TERMS: dict[str, str] = {
    "who came in": "Asks for individual patient identities",
    "who checked into": "Asks for individual patient identities",
    "everyone admitted": "Requests patient-level records",
    "people seen": "Requests patient-level records",
    "which records": "Attempts re-identification through records",
    "person tied to": "Requests identity through an encounter identifier",
    "reverse lookup": "Attempts identity resolution through an internal identifier",
}

PATIENT_RANKING_TERMS: dict[str, str] = {
    "frequent visitors": "Requests ranked patient-level information",
    "frequent flyers": "Requests ranked patient-level information",
    "high utilizers": "Requests ranked patient-level information",
    "rank patients": "Requests ranked patient-level information",
    "top patients": "Requests ranked patient-level information",
}

ROW_LEVEL_VERBS = {
    "list",
    "show",
    "show me",
    "give me",
    "export",
    "download",
    "return",
    "print",
    "dump",
}

ROW_LEVEL_OBJECTS = {
    "patient",
    "patients",
    "patient record",
    "patient records",
    "row",
    "rows",
    "record",
    "records",
    "raw record",
    "raw records",
    "encounter row",
    "encounter rows",
    "encounter record",
    "encounter records",
}

AGGREGATE_KEYWORDS = {
    "count",
    "sum",
    "avg",
    "average",
    "min",
    "max",
    "median",
    "percentile",
    "rate",
    "ratio",
    "trend",
    "total",
    "aggregate",
    "group by",
    "distribution",
    "summary",
    "breakdown",
    "compare",
}

MIXED_ROW_LEVEL_MARKERS = {
    "row",
    "rows",
    "record",
    "records",
    "raw",
    "individual",
    "each",
    "every",
    "person",
    "people",
}

FUZZY_HIGH_RISK_TERMS: dict[str, str] = {
    "patient": "Likely typo for patient-identifying information",
    "patients": "Likely typo for patient-identifying information",
    "phone": "Likely typo for patient contact information",
    "address": "Likely typo for patient address information",
    "password": "Likely typo for secret or credential",
    "credential": "Likely typo for secret or credential",
    "secret": "Likely typo for secret or credential",
    "which": "Likely typo for an individual patient request",
    "delete": "Likely typo for a destructive database action",
    "drop": "Likely typo for a destructive database action",
    "update": "Likely typo for a destructive database action",
    "insert": "Likely typo for a destructive database action",
    "api": "Likely typo for a secret or credential",
    "key": "Likely typo for a secret or credential",
}

FUZZY_TERM_DISTANCE_OVERRIDES: dict[str, int] = {
    "api": 1,
    "key": 1,
    "drop": 1,
}


def check_small_cell_risk(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, SMALL_CELL_RISK_TERMS)


def check_known_person_lookup(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, KNOWN_PERSON_LOOKUP_TERMS)


def check_identifiers(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, IDENTIFIER_TERMS)


def check_individual_request(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, INDIVIDUAL_REQUEST_TERMS)


def check_indirect_identity(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, INDIRECT_IDENTITY_TERMS)


def check_patient_ranking(text: str, q: str) -> PolicyGateResult | None:
    return match_terms(text, q, PATIENT_RANKING_TERMS)


def check_fuzzy_terms(q: str) -> PolicyGateResult | None:
    leetspeak_q = normalize_prompt(normalize_leetspeak(q))
    for word in leetspeak_q.split():
        if word in FUZZY_HIGH_RISK_TERMS:
            continue

        for term, reason in FUZZY_HIGH_RISK_TERMS.items():
            max_distance = max_typo_distance(term)
            if abs(len(word) - len(term)) > max_distance:
                continue

            distance = damerau_levenshtein(word, term)
            if 0 < distance <= max_distance:
                return blocked(reason=reason, matched_term=term)

    return None


def check_row_level_request(q: str) -> PolicyGateResult | None:
    if is_schema_metadata_request(q):
        return None

    has_aggregate_keyword = any(
        contains_phrase(q, term) for term in AGGREGATE_KEYWORDS
    )
    has_unsafe_verb = any(contains_phrase(q, term) for term in ROW_LEVEL_VERBS)
    has_row_object = any(contains_phrase(q, term) for term in ROW_LEVEL_OBJECTS)
    has_explicit_row_level_marker = any(
        contains_phrase(q, term) for term in MIXED_ROW_LEVEL_MARKERS
    )

    # Aggregate metrics commonly mention patients (for example, "average
    # length of stay for patients").  Require an explicit row-level marker
    # before treating a mixed aggregate request as disclosure-oriented.
    if has_unsafe_verb and has_row_object and (
        not has_aggregate_keyword or has_explicit_row_level_marker
    ):
        return blocked("Requests row-level patient or encounter data", "row-level request")

    # Aggregate wording does not make a mixed request safe.  Check for the
    # stronger row-level request first, then allow aggregate-only analytics.
    if has_aggregate_keyword:
        return None

    return None


def match_terms(
    text: str, q: str, terms: dict[str, str]
) -> PolicyGateResult | None:
    for term, reason in terms.items():
        if matches_blocked_term(text, q, term):
            return blocked(reason=reason, matched_term=term)
    return None


def max_typo_distance(term: str) -> int:
    if term in FUZZY_TERM_DISTANCE_OVERRIDES:
        return FUZZY_TERM_DISTANCE_OVERRIDES[term]
    if len(term) <= 4:
        return 0
    if len(term) <= 7:
        return 1
    return 2


def damerau_levenshtein(a: str, b: str) -> int:
    rows = len(a) + 1
    cols = len(b) + 1
    distances = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distances[i][0] = i
    for j in range(cols):
        distances[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            distances[i][j] = min(
                distances[i - 1][j] + 1,
                distances[i][j - 1] + 1,
                distances[i - 1][j - 1] + cost,
            )
            if (
                i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                distances[i][j] = min(
                    distances[i][j],
                    distances[i - 2][j - 2] + 1,
                )
    return distances[-1][-1]
