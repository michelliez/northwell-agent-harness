"""Host-authored answers for runs that end without a real result.

Every operational dead end states which stage stopped, why in plain terms,
and one concrete reformulation the user can try next. The strings are fully
deterministic: they never interpolate retrieved or blocked content, and they
are never model-generated. Policy refusals deliberately live elsewhere
(``intent_nodes._REFUSAL_RESPONSES``) and carry no rephrase guidance.

All of these cross the final-answer content screen, so wording must avoid
row-level phrasings and identifier-value patterns (see ``policy.screen``).
"""

from __future__ import annotations

RETRIEVAL_INDEX_UNAVAILABLE = (
    "I couldn't search the documentation because the index is unavailable. "
    "This is a configuration problem, not an issue with your question - "
    "please try again once the index is restored, or contact the system "
    "administrator."
)

RETRIEVAL_CONTENT_BLOCKED = (
    "I found documentation, but every retrieved passage was withheld by the "
    "content screen, so I couldn't use any of it. Try narrowing the question "
    "to one table or column - for example: 'What is the PAT_ENC table?'"
)

NO_DOCUMENTATION_FOUND = (
    "I searched the documentation and couldn't find anything relevant to "
    "your question. Try naming a specific table or column - for example: "
    "'What is the PAT_ENC table?' or 'Which tables track hospital admissions?'"
)

CLARIFICATION_PROMPT = (
    "I couldn't find relevant documentation for that. Could you point me at "
    "a specific table, column, or topic? A well-formed example: 'Which "
    "tables track hospital admissions?'"
)

CLARIFICATION_EXHAUSTED = (
    "I wasn't able to pin down what you're asking after several attempts. "
    "Please start a fresh question in one sentence naming a table, column, "
    "or metric - for example: 'What is the PAT_ENC table?'"
)

SCHEMA_EVIDENCE_INTERNAL_ERROR = (
    "I retrieved documentation but hit an internal error while assembling "
    "schema evidence from it, so I can't plan SQL for this question right "
    "now. This is a bug on my side, not a problem with your question - "
    "please try once more, and report it if it persists."
)

SQL_NO_SCHEMA_EVIDENCE = (
    "I couldn't gather enough schema evidence to plan SQL for this. Name "
    "the documented table you want to query - for example: 'Write SQL to "
    "count rows in PAT_ENC_HSP.'"
)

SQL_PLANNER_UNAVAILABLE = (
    "I stopped while turning your question into a query plan because the "
    "planning step failed. Try again as one plain sentence naming the table "
    "and the aggregate you want - for example: 'Write SQL to count hospital "
    "encounters in PAT_ENC_HSP by admission date.'"
)

SQL_PIPELINE_STATE_MISSING = (
    "I stopped while drafting SQL because an earlier step didn't produce a "
    "usable query plan. Please ask again in one sentence naming the table "
    "and the aggregate you want."
)

SQL_COMPILER_UNSUPPORTED = (
    "The approved query plan needs a SQL feature the deterministic compiler "
    "doesn't support yet ({code}). Simplify the request - a single aggregate "
    "with at most one breakdown works best."
)
