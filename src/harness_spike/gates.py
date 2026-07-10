from __future__ import annotations

from typing import TypedDict
import unicodedata
import re


class PolicyGateResult(TypedDict):
    allowed: bool
    reason: str | None
    matched_term: str | None


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
    "member id": "Requests patient-identifying information",
    "account number": "Requests patient-identifying information",
    "mrn": "Requests medical record numbers",
    "mrns": "Requests medical record numbers",
    "medical record number": "Requests medical record numbers",
    "medical record numbers": "Requests medical record numbers",
    "member id": "Requests member identifiers",
    "member ids": "Requests member identifiers",
    "account number": "Requests account identifiers",
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

PATIENT_RANKING_TERMS: dict[str, str] = {
    "frequent visitors": "Requests ranked patient-level information",
    "frequent flyers": "Requests ranked patient-level information",
    "high utilizers": "Requests ranked patient-level information",
    "rank patients": "Requests ranked patient-level information",
    "top patients": "Requests ranked patient-level information",
}

DESTRUCTIVE_DB_TERMS: dict[str, str] = {
    "delete": "Requests a destructive database action",
    "drop": "Requests a destructive database action",
    "update": "Requests a destructive database action",
    "truncate": "Requests a destructive database action",
    "alter": "Requests a destructive database action",
    "merge": "Requests a destructive database action",
    "upsert": "Requests a destructive database action",
    "overwrite": "Requests a destructive database action",
    "wipe": "Requests a destructive database action",
    "purge": "Requests a destructive database action",
    "remove": "Requests a destructive database action",
    "grant": "Requests a destructive database action",
    "revoke": "Requests a destructive database action",
    "insert": "Requests a destructive database action",
}

POLICY_MANIPULATION_TERMS: dict[str, str] = {
    "ignore policy": "Tries to change rules",
    "ignore permissions": "Tries to change rules",
    "bypass": "Tries to change rules",
    "override": "Tries to change rules",
    "disable safety": "Tries to change rules",
    "forget rules": "Tries to change rules",
    "policy does not apply": "Tries to change rules",
    "pretend you": "Tries to change rules",
    "pretend this is allowed": "Tries to change rules",
    "pretend you are allowed": "Tries to change rules",
    "pretend you're allowed": "Tries to change rules",
    "for testing only": "Tries to override policy",
    "for audit purposes": "Tries to override policy",
    "i am authorized": "Tries to override policy",
}

SECRET_ACCESS_TERMS: dict[str, str] = {
    "api key": "Requests a secret or credential",
    "api keys": "Requests a secret or credential",
    "password": "Requests a secret or credential",
    "passwords": "Requests a secret or credential",
    "credential": "Requests a secret or credential",
    "credentials": "Requests a secret or credential",
    "secret": "Requests a secret or credential",
    "secrets": "Requests a secret or credential",
    "local files": "Requests access to local files",
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

BROAD_DATA_EXPOSURE_TERMS: dict[str, str] = {
    "every field": "Requests broad row-level data exposure",
    "everything you have": "Requests broad row-level data exposure",
    "raw records": "Requests raw row-level records",
    "don't aggregate": "Requests non-aggregate output",
    "do not aggregate": "Requests non-aggregate output",
    "no summary": "Requests non-aggregate output",
    "all patient fields": "Requests broad patient data exposure",
    "json key": "Attempts to hide sensitive output",
    "initials": "Requests identifying patient-level information",
}

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

TOOL_BYPASS_TERMS: dict[str, str] = {
    "previous rules": "Attempts to bypass policy rules",
    "manager approved": "Attempts permission escalation",
    "policy gate already passed": "Attempts to skip a policy node",
    "execution step": "Attempts to bypass validation nodes",
    "safety checks": "Attempts to disable guardrails",
    "not listed": "Attempts to invoke unregistered capabilities",
    "new tool": "Attempts dynamic capability creation",
    "another agent": "Attempts privilege escalation through agent spawning",
    "broader permissions": "Attempts privilege escalation",
    "repeatedly": "Attempts to bypass bounded retry limits",
    "retry forever": "Requests an unbounded execution loop",
    "without logging": "Attempts to bypass audit requirements",
}

SCOPE_EXPANSION_TERMS: dict[str, str] = {
    "patients table": "Attempts to broaden data access",
    "restricted": "Attempts to access restricted data",
    "hidden columns": "Attempts to access fields outside the approved schema",
    "any table you need": "Attempts to broaden data access",
}

LOCAL_EXECUTION_AND_EXFILTRATION_TERMS: dict[str, str] = {
    "shell command": "Requests command execution outside allowed tools",
    ".env": "Requests secrets from local files",
    "environment variables": "Requests credentials and internal configuration",
    "external webhook": "Requests unauthorized network exfiltration",
    "local csv": "Requests file-writing capability",
}

KNOWN_PERSON_LOOKUP_TERMS: dict[str, str] = {
    "celebrity": "Requests confirmation of an individual's health-system presence",
    "coworker": "Requests sensitive information about a known person",
    "was anyone from": "Potentially identifying query through location and time",
}

BLOCKED_TERM_CATEGORIES: tuple[dict[str, str], ...] = (
    POLICY_MANIPULATION_TERMS,
    SECRET_ACCESS_TERMS,
    TOOL_BYPASS_TERMS,
    LOCAL_EXECUTION_AND_EXFILTRATION_TERMS,
    SCOPE_EXPANSION_TERMS,
    DESTRUCTIVE_DB_TERMS,
    BROAD_DATA_EXPOSURE_TERMS,
    SMALL_CELL_RISK_TERMS,
    KNOWN_PERSON_LOOKUP_TERMS,
    IDENTIFIER_TERMS,
    INDIVIDUAL_REQUEST_TERMS,
    INDIRECT_IDENTITY_TERMS,
    PATIENT_RANKING_TERMS,
)

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

ALLOWED_AGGREGATE_TERMS = {
    "how many",
    "count",
    "average",
    "median",
    "rate",
    "percentage",
    "total",
    "group by",
    "trend",
    "aggregate",
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

#Policy Gate
def policy_gate(question: str) -> PolicyGateResult:
    """Check whether a prompt is allowed before routing to tools or the model."""
    q = normalize_prompt(question)

    for terms in BLOCKED_TERM_CATEGORIES:
        for term, reason in terms.items():
            if matches_blocked_term(question, q, term):
                return blocked(reason=reason, matched_term=term)
            
    fuzzy_result = match_fuzzy_terms(q)
    if fuzzy_result is not None:
        return fuzzy_result

    row_level_result = match_row_level_request(q)
    if row_level_result is not None:
        return row_level_result

    return allowed()


#Helpers

def blocked(reason: str, matched_term: str) -> PolicyGateResult:
    return {
        "allowed": False,
        "reason": reason,
        "matched_term": matched_term,
    }

def allowed() -> PolicyGateResult:
    return {
        "allowed": True,
        "reason": None,
        "matched_term": None,
    }

#Normalization
ZERO_WIDTH_CHARS = {
    "\u200b",  # zero-width space
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u00ad",  # soft hyphen
    "\u00a0",  # nonbreaking space
}

LEETSPEAK_TRANSLATION = str.maketrans({
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "@": "a",
    "$": "s",
})


def normalize_prompt(text: str) -> str:
    q = unicodedata.normalize("NFKC", text) #remove unicode chars
    q = q.lower()
    for char in ZERO_WIDTH_CHARS: #remove invisible chars
        q = q.replace(char, " ")
    q = re.sub(r"[^a-z0-9]+", " ", q) #removes characters that shouldn't be there
    q = " ".join(q.split()) # remove spaces    
    return q

def compact_prompt(text: str) -> str:
    q = unicodedata.normalize("NFKC", text)
    q = q.lower()
    for char in ZERO_WIDTH_CHARS:
        q = q.replace(char, "")
    return re.sub(r"[^a-z0-9]", "", q)

def normalize_leetspeak(text: str) -> str:
    return text.translate(LEETSPEAK_TRANSLATION)


#Prohibits requests that have unsafe verbs and is related to a row object.
def match_row_level_request(q: str) -> PolicyGateResult | None:
    has_unsafe_verb = any(contains_phrase(q, term) for term in ROW_LEVEL_VERBS)
    has_row_object = any(contains_phrase(q, term) for term in ROW_LEVEL_OBJECTS)

    if has_unsafe_verb and has_row_object:
        return blocked("Requests row-level patient or encounter data", "row-level request")

    return None

def contains_phrase(q: str, term: str) -> bool:
    return f" {term} " in f" {q} "

def matches_blocked_term(text: str, q: str, term: str) -> bool:
    if contains_phrase(q, term):
        return True

    leetspeak_q = normalize_prompt(normalize_leetspeak(text))
    if contains_phrase(leetspeak_q, term):
        return True

    return contains_obfuscated_term(text, term)

# Catch punctuation/spacing inserted inside blocked terms, such as D.R.O.P or m r n, without matching harmless larger words like emergency containing merge.
def contains_obfuscated_term(text: str, term: str) -> bool:
    compact_term = compact_prompt(term)
    if not compact_term:
        return False

    normalized_text = unicodedata.normalize("NFKC", text).lower()
    for char in ZERO_WIDTH_CHARS:
        normalized_text = normalized_text.replace(char, " ")

    pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]*".join(
        re.escape(char) for char in compact_term
    ) + r"(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None

#check for spelling errors
def match_fuzzy_terms(q: str) -> PolicyGateResult | None:
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
