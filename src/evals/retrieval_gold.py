from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from retrieval.mcp_server import search_ranked_chunks


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    query: str
    category: str
    answerable: bool
    evaluation_scope: str
    completeness_method: str
    variants: tuple[str, str]
    scope_where: str
    scope_params: tuple[str, ...]
    grades: dict[str, int]
    rationales: dict[str, str]


def _specs() -> tuple[QuerySpec, ...]:
    return (
        QuerySpec(
            query_id="J01",
            query="What columns form the primary key of ACCESS_LOG?",
            category="primary_key",
            answerable=True,
            evaluation_scope="All chunks in ACCESS_LOG.html.",
            completeness_method=(
                "Enumerated every ACCESS_LOG.html chunk and verified the single Primary-Key "
                "section against the Column-Information section."
            ),
            variants=(
                "ACCESS_LOG composite key fields",
                "fields that uniquely identify an ACCESS_LOG row",
            ),
            scope_where="d.source_path = ?",
            scope_params=("ACCESS_LOG.html",),
            grades={
                "6221d97cba59bf3d4d8b2bcb73ae863c4424ef426335465522bd2a2bc856be64": 3,
                "7f225a037d0220c1e797fcf6764553c705396b4b64df05354a990cd689f03a04": 2,
                "fd105fdc074c49dc0c5cf992015593ff13f24a593e86378ba31172a681f5c993": 1,
            },
            rationales={
                "6221d97cba59bf3d4d8b2bcb73ae863c4424ef426335465522bd2a2bc856be64": (
                    "The ACCESS_LOG Primary-Key section explicitly lists ACCESS_TIME, "
                    "PROCESS_ID, and ACCESS_INSTANT in ordinal order."
                ),
                "7f225a037d0220c1e797fcf6764553c705396b4b64df05354a990cd689f03a04": (
                    "The ACCESS_LOG Column-Information section defines all three key columns "
                    "but does not itself state that they form the primary key."
                ),
                "fd105fdc074c49dc0c5cf992015593ff13f24a593e86378ba31172a681f5c993": (
                    "The metadata identifies ACCESS_LOG and its audit-table purpose but does "
                    "not list its primary-key columns."
                ),
            },
        ),
        QuerySpec(
            query_id="J02",
            query=(
                "What is the data type and description of the ABSTRACTOR_COMMENTS column "
                "in the ABSTRACTOR_COMMENTS table?"
            ),
            category="column_definition",
            answerable=True,
            evaluation_scope="All chunks in ABSTRACTOR_COMMENTS.html.",
            completeness_method=(
                "Enumerated every ABSTRACTOR_COMMENTS.html chunk and located the named column "
                "in the complete Column-Information section."
            ),
            variants=(
                "ABSTRACTOR_COMMENTS field SQL type meaning",
                "comments entered by abstractors registry record column datatype",
            ),
            scope_where="d.source_path = ?",
            scope_params=("ABSTRACTOR_COMMENTS.html",),
            grades={
                "85cf398ca5037186113e3b85ca51ea23cffd4f71e241290864007c47ed359530": 3,
                "8f176f5531e03474a703e7b43323fe852c2c52dcde94ec6ebe272dada495dc14": 1,
            },
            rationales={
                "85cf398ca5037186113e3b85ca51ea23cffd4f71e241290864007c47ed359530": (
                    "The Column-Information section gives VARCHAR(254) and describes comments "
                    "entered by abstractors about a registry record."
                ),
                "8f176f5531e03474a703e7b43323fe852c2c52dcde94ec6ebe272dada495dc14": (
                    "The table metadata repeats the business meaning of the comments but does "
                    "not provide the column data type."
                ),
            },
        ),
        QuerySpec(
            query_id="J03",
            query=(
                "Among ACCESS-prefixed tables, which extracted base table records the user "
                "and time for patient-record access events?"
            ),
            category="synonym_match",
            answerable=True,
            evaluation_scope="All 20 documents whose source path matches ACCESS%.html.",
            completeness_method=(
                "Enumerated all ACCESS%.html documents and every chunk within them; reviewed "
                "metadata and column definitions for access-event, user, patient, and time evidence."
            ),
            variants=(
                "ACCESS tables patient chart audit user timestamp",
                "ACCESS-prefixed access history activity who when patient",
            ),
            scope_where="d.source_path LIKE ?",
            scope_params=("ACCESS%.html",),
            grades={
                "fd105fdc074c49dc0c5cf992015593ff13f24a593e86378ba31172a681f5c993": 3,
                "7f225a037d0220c1e797fcf6764553c705396b4b64df05354a990cd689f03a04": 3,
                "6221d97cba59bf3d4d8b2bcb73ae863c4424ef426335465522bd2a2bc856be64": 1,
            },
            rationales={
                "fd105fdc074c49dc0c5cf992015593ff13f24a593e86378ba31172a681f5c993": (
                    "ACCESS_LOG metadata identifies it as the extracted APPEND/AUDIT table "
                    "containing basic Access History activity information and event time."
                ),
                "7f225a037d0220c1e797fcf6764553c705396b4b64df05354a990cd689f03a04": (
                    "ACCESS_LOG columns include USER_ID, PAT_ID, ACCESS_TIME, and ACCESS_INSTANT, "
                    "directly establishing who, which patient, and when."
                ),
                "6221d97cba59bf3d4d8b2bcb73ae863c4424ef426335465522bd2a2bc856be64": (
                    "The ACCESS_LOG primary key includes the event time fields and process ID, "
                    "providing supporting event-grain evidence but not the user field."
                ),
            },
        ),
        QuerySpec(
            query_id="J04",
            query=(
                "Among ABO-prefixed tables, which stores facility-level setup for determining "
                "ABO blood group rather than Rh factor from lab-result components?"
            ),
            category="synonym_match",
            answerable=True,
            evaluation_scope="Both documents whose source path matches ABO%.html.",
            completeness_method=(
                "Enumerated both ABO%.html documents (ABO_SETUP and ABO_RH_SETUP) and every "
                "chunk within them; compared their descriptions and component columns."
            ),
            variants=(
                "ABO tables facility blood type lab component setup",
                "ABO prefix determine patient blood group result component not Rh",
            ),
            scope_where="d.source_path LIKE ?",
            scope_params=("ABO%.html",),
            grades={
                "23c36a483d127814851422c550c9aef10b8b0d5b69b978f2221b276fc50c4815": 3,
                "d407ae1a9a940f9c14f74cf9386af1fe3fc42609089cd03fc31c8d86feaeb92f": 3,
                "fc949a2c9d44c09d295e14b0aeae4d35508699bf77aadfad080e2e4f5c6daaca": 2,
                "ed888bc91a40b914d727584a5ea99cc806d0629e594e312c1709c01946b68f00": 1,
            },
            rationales={
                "23c36a483d127814851422c550c9aef10b8b0d5b69b978f2221b276fc50c4815": (
                    "ABO_SETUP metadata says it contains result-component information used to "
                    "determine the patient's ABO type."
                ),
                "d407ae1a9a940f9c14f74cf9386af1fe3fc42609089cd03fc31c8d86feaeb92f": (
                    "ABO_SETUP columns include facility identifier FAC_ID and ABO component "
                    "configuration, directly supporting the requested setup."
                ),
                "fc949a2c9d44c09d295e14b0aeae4d35508699bf77aadfad080e2e4f5c6daaca": (
                    "ABO_RH_SETUP metadata is the explicit Rh-factor companion and therefore "
                    "provides strong contrast evidence for choosing ABO_SETUP."
                ),
                "ed888bc91a40b914d727584a5ea99cc806d0629e594e312c1709c01946b68f00": (
                    "ABO_RH_SETUP columns confirm facility-level Rh-component configuration, "
                    "useful only as supporting contrast."
                ),
            },
        ),
        QuerySpec(
            query_id="J05",
            query=(
                "Among all tables with names beginning with ABST in this corpus, which have "
                "REGISTRY_DATA_ID and LINE as their complete primary key?"
            ),
            category="cross_table",
            answerable=True,
            evaluation_scope="All 9 documents whose source path matches ABST%.html.",
            completeness_method=(
                "Enumerated all ABST%.html documents and verified each complete Primary-Key "
                "section; exactly nine documents exist and all nine have the two requested columns."
            ),
            variants=(
                "ABST tables REGISTRY_DATA_ID LINE primary key",
                "tables starting ABST compound key registry data line",
            ),
            scope_where="d.source_path LIKE ?",
            scope_params=("ABST%.html",),
            grades={
                "228cd93830216db0f0b4ac47cbd9c10755aeafe826c5c13e03ad7691e8d7d094": 3,
                "6bca22b958e174d50f418028645fbf7ba7e09f3138f547f5bc65ca824b5ec115": 3,
                "fdc9942f0d2b9c38ebce788b785b2bc194ef4102aace93a635d8ca4b349e3be9": 3,
                "44ffcaf284b2acff226caadfd570c5be2a955d3649d5b4824a36927a75b5291e": 3,
                "2ff423cdcfd203489d6686637b6842fe328116dba3f141baf8ff02d3a89878f6": 3,
                "ed8de9fa79ff1d76dacc675c01fdd6ab3228b40638dfb107a61ade9fec69775c": 3,
                "005481045738ae2d87c65d556ec508e551e2fb922fc95eab90a3eba28c374e74": 3,
                "3e8687589e274bbfdc635625d135c336fb6d5cb0deefbafda7ea631ca7835bb8": 3,
                "ad36e2b76eef1bba9c41ae13d774f6b99b6e36c027bcacba417ad57244cd1d26": 3,
            },
            rationales={},
        ),
        QuerySpec(
            query_id="J06",
            query="What is the definition of the ENCOUNTER_DATE column in ACCESS_LOG?",
            category="unanswerable",
            answerable=False,
            evaluation_scope="All chunks in ACCESS_LOG.html.",
            completeness_method=(
                "Enumerated the complete ACCESS_LOG Column-Information section: its 11 columns "
                "do not include ENCOUNTER_DATE."
            ),
            variants=(
                "ACCESS_LOG ENCOUNTER_DATE meaning",
                "encounter date field in access audit log",
            ),
            scope_where="d.source_path = ?",
            scope_params=("ACCESS_LOG.html",),
            grades={},
            rationales={},
        ),
        QuerySpec(
            query_id="J07",
            query=(
                "In ORDERS, what records does the table contain, and which column associates "
                "an order with its patient?"
            ),
            category="schema_summary",
            answerable=True,
            evaluation_scope="All 8 chunks in ORDERS.html.",
            completeness_method=(
                "Enumerated every ORDERS.html chunk and reviewed its metadata, complete "
                "Column-Information section, and PAT_ID foreign-key entries."
            ),
            variants=(
                "ORDERS generic order records patient link field",
                "how orders of every type connect to the patient record",
            ),
            scope_where="d.source_path = ?",
            scope_params=("ORDERS.html",),
            grades={
                "eb8dbd1527e35ffcc90df165f09ad013a8237e040337bea163ddca0b0ce72adc": 3,
                "d48974b32a4a0a7b3267aa7644d0b40e61debae26ecf175f62126e424f31c196": 3,
                "5108e2134fb8f15bb12b49cacde86f334be0eaf7a5a35de88506e7be3374939d": 2,
                "b9e78cb5dfca07be5e72a52cac10f4e0aadd688015425f093013e5a1e8629e63": 2,
                "825c9c2d3f9f28c7c90b27e3c4a94b3e872e4bc4fe109b8895b1fd05d29b6b72": 2,
            },
            rationales={
                "eb8dbd1527e35ffcc90df165f09ad013a8237e040337bea163ddca0b0ce72adc": (
                    "ORDERS metadata says the table contains every order record regardless of "
                    "type and includes the associated patient record."
                ),
                "d48974b32a4a0a7b3267aa7644d0b40e61debae26ecf175f62126e424f31c196": (
                    "The Column-Information section defines PAT_ID as the unique ID of the "
                    "patient record associated with the order."
                ),
                "5108e2134fb8f15bb12b49cacde86f334be0eaf7a5a35de88506e7be3374939d": (
                    "This foreign-key chunk shows PAT_ID relationships, including the PATIENT table."
                ),
                "b9e78cb5dfca07be5e72a52cac10f4e0aadd688015425f093013e5a1e8629e63": (
                    "This foreign-key continuation explicitly maps ORDERS.PAT_ID to PATIENT.PAT_ID."
                ),
                "825c9c2d3f9f28c7c90b27e3c4a94b3e872e4bc4fe109b8895b1fd05d29b6b72": (
                    "This foreign-key chunk also explicitly maps PAT_ID to the PATIENT table."
                ),
            },
        ),
        QuerySpec(
            query_id="J08",
            query=(
                "In PAT_PCP, which columns identify the patient and provider, and which dates "
                "define when that provider relationship is active?"
            ),
            category="relationship_columns",
            answerable=True,
            evaluation_scope="All 9 chunks in PAT_PCP.html.",
            completeness_method=(
                "Enumerated every PAT_PCP.html chunk and reviewed the complete column list plus "
                "patient and provider foreign-key sections."
            ),
            variants=(
                "PAT_PCP patient provider identifiers effective termination dates",
                "fields linking a patient to a PCP and the active date range",
            ),
            scope_where="d.source_path = ?",
            scope_params=("PAT_PCP.html",),
            grades={
                "3616db22385c52f255a6603afb9148d092e2300b009aa05e4c38efd6ad750ec9": 3,
                "21beb317f410eb0d1c028755dd740ebfe8392adcffd125f45e633095553ffe36": 1,
                "2c588cd2204e947d4ac15c0c13a49dda6bc7e1c25abea3a21c59ef44bd6d2b9f": 1,
                "b96d23344c208642508f08924ffd71419f5050b20e74c54635acb6a0c81453cf": 2,
                "3909fef36834852b7840595af5004ff8ad71bea7d41f0e861afeb03f4adc6394": 1,
            },
            rationales={
                "3616db22385c52f255a6603afb9148d092e2300b009aa05e4c38efd6ad750ec9": (
                    "The Column-Information chunk directly defines PAT_ID, PCP_PROV_ID, "
                    "EFF_DATE, and TERM_DATE."
                ),
                "21beb317f410eb0d1c028755dd740ebfe8392adcffd125f45e633095553ffe36": (
                    "PAT_PCP metadata establishes that the table stores patient PCP and care-team "
                    "relationships over time, but it does not name all requested columns."
                ),
                "2c588cd2204e947d4ac15c0c13a49dda6bc7e1c25abea3a21c59ef44bd6d2b9f": (
                    "The primary key confirms PAT_ID as the patient-side row identifier but does "
                    "not provide the provider or active dates."
                ),
                "b96d23344c208642508f08924ffd71419f5050b20e74c54635acb6a0c81453cf": (
                    "The foreign-key section maps PAT_ID to PATIENT and PCP_PROV_ID to provider "
                    "tables, confirming both relationship identifiers."
                ),
                "3909fef36834852b7840595af5004ff8ad71bea7d41f0e861afeb03f4adc6394": (
                    "This continuation provides additional PCP_PROV_ID provider mappings but no "
                    "date-range definitions."
                ),
            },
        ),
        QuerySpec(
            query_id="J09",
            query=(
                "Between CLM_DX and CLM_PX, which table stores claim diagnoses and which stores "
                "inpatient ICD procedures, and how is the principal item represented in each?"
            ),
            category="table_comparison",
            answerable=True,
            evaluation_scope="All 8 chunks across CLM_DX.html and CLM_PX.html.",
            completeness_method=(
                "Enumerated every chunk in both documents and compared their metadata and complete "
                "column definitions for diagnosis, procedure, and principal-item behavior."
            ),
            variants=(
                "CLM_DX diagnoses versus CLM_PX inpatient procedures principal line",
                "claim diagnosis table and ICD surgical procedure table first line",
            ),
            scope_where="d.source_path IN (?, ?)",
            scope_params=("CLM_DX.html", "CLM_PX.html"),
            grades={
                "fece78f87a546cb545c70841b6271b76746e7598a5195f9ddf385df8b2e0d3ec": 3,
                "3ca331174634ff70cee7923a1434d75cdfce0f28fa1ae68c2edcdf31a5a92cea": 3,
                "fe7956f13d461f45eef849654fd0edc303a30d5db56c40e00806a011e9fef433": 3,
                "b8292f4811a1dd642c12fbd2a4a0d0e78ade3aa178b7de74bf675161e3f7aefa": 3,
            },
            rationales={
                "fece78f87a546cb545c70841b6271b76746e7598a5195f9ddf385df8b2e0d3ec": (
                    "CLM_DX metadata identifies it as the table holding diagnoses for a claim."
                ),
                "3ca331174634ff70cee7923a1434d75cdfce0f28fa1ae68c2edcdf31a5a92cea": (
                    "CLM_DX column definitions state that the principal diagnosis is on the first "
                    "line and other diagnoses follow."
                ),
                "fe7956f13d461f45eef849654fd0edc303a30d5db56c40e00806a011e9fef433": (
                    "CLM_PX metadata identifies it as the table holding ICD procedures that "
                    "document surgical procedures on inpatient claims."
                ),
                "b8292f4811a1dd642c12fbd2a4a0d0e78ade3aa178b7de74bf675161e3f7aefa": (
                    "CLM_PX column definitions state that the principal procedure is on the first "
                    "line and other procedures follow."
                ),
            },
        ),
        QuerySpec(
            query_id="J10",
            query=(
                "In ZC_APP, which APP_C values represent MyChart on iPhone, MyChart on Android, "
                "and Apple Wallet?"
            ),
            category="category_value_lookup",
            answerable=True,
            evaluation_scope="All 3 chunks in ZC_APP.html.",
            completeness_method=(
                "Enumerated every ZC_APP.html chunk and reviewed the complete APP_C category "
                "entries in its Column-Information section."
            ),
            variants=(
                "ZC_APP codes MyChart iPhone Android Apple Wallet",
                "mobile application category IDs for MyChart and AppleWallet",
            ),
            scope_where="d.source_path = ?",
            scope_params=("ZC_APP.html",),
            grades={
                "fd22b68fb20247e11cc4a954a77a516470813fe469951acff1866ee196d533e6": 3,
                "f938e3706976682282f50a645234100c5fea713460dde330c3effabf33045f12": 1,
            },
            rationales={
                "fd22b68fb20247e11cc4a954a77a516470813fe469951acff1866ee196d533e6": (
                    "The APP_C category entries explicitly map 1 to EpicMyChart-iPhone, 2 to "
                    "EpicMyChart-Android, and 10 to AppleWallet."
                ),
                "f938e3706976682282f50a645234100c5fea713460dde330c3effabf33045f12": (
                    "The primary-key section identifies APP_C as the category key but does not "
                    "contain the requested value mappings."
                ),
            },
        ),
    )


def _chunk_row(conn: sqlite3.Connection, chunk_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.category, c.heading_path, d.source_path
        FROM chunks AS c
        JOIN docs AS d ON d.doc_id = c.doc_id
        WHERE c.chunk_id = ?
        """,
        (chunk_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"gold judgment references missing chunk {chunk_id}")
    return row


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def build_gold_set(
    db_path: Path,
    queries_path: Path,
    qrels_path: Path,
    *,
    fts_top_k: int = 50,
) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        metadata = dict(conn.execute("SELECT key, value FROM index_metadata"))
        index_version = metadata["index_version"]
        query_rows: list[dict[str, object]] = []
        qrel_rows: list[dict[str, object]] = []

        for spec in _specs():
            pool: dict[str, set[str]] = {}
            search_queries = (spec.query, *spec.variants)
            for position, search_query in enumerate(search_queries):
                provenance = "fts_original" if position == 0 else f"fts_synonym_{position}"
                results = search_ranked_chunks(
                    conn.cursor(), query=search_query, top_k=fts_top_k
                )
                for result in results:
                    pool.setdefault(str(result["chunk_id"]), set()).add(provenance)

            exact_rows = conn.execute(
                f"""
                SELECT c.chunk_id
                FROM chunks AS c
                JOIN docs AS d ON d.doc_id = c.doc_id
                WHERE {spec.scope_where}
                """,
                spec.scope_params,
            ).fetchall()
            for row in exact_rows:
                pool.setdefault(str(row["chunk_id"]), set()).add("exact_scope_sql")

            missing_gold = set(spec.grades) - set(pool)
            if missing_gold:
                raise RuntimeError(f"{spec.query_id} gold chunks missing from pool: {missing_gold}")
            if spec.answerable != bool(spec.grades):
                raise RuntimeError(f"{spec.query_id} answerability disagrees with positive labels")

            eligible_metrics = (
                ["recall", "precision", "ndcg", "mrr"]
                if spec.answerable
                else ["no_answer_accuracy"]
            )
            query_rows.append(
                {
                    "query_id": spec.query_id,
                    "query": spec.query,
                    "category": spec.category,
                    "answerable": spec.answerable,
                    "evaluation_scope": spec.evaluation_scope,
                    "ground_truth_complete": True,
                    "completeness_method": spec.completeness_method,
                    "index_version": index_version,
                    "candidate_pool": {
                        "fts_top_k_per_query": fts_top_k,
                        "synonym_queries": list(spec.variants),
                        "exact_lookup": spec.scope_where,
                        "candidate_count": len(pool),
                    },
                    "pooling_complete_for": ["sqlite_fts", "exact_sql"],
                    "pooling_pending_for": ["embedding", "hybrid"],
                    "eligible_metrics": eligible_metrics,
                }
            )

            for chunk_id in sorted(pool):
                chunk = _chunk_row(conn, chunk_id)
                relevance = spec.grades.get(chunk_id, 0)
                rationale = spec.rationales.get(chunk_id)
                if rationale is None and relevance == 3 and spec.query_id == "J05":
                    rationale = (
                        f"{chunk['source_path']} Primary-Key explicitly lists "
                        "REGISTRY_DATA_ID first and LINE second, with no additional key columns."
                    )
                if rationale is None and spec.query_id == "J06":
                    rationale = (
                        "Reviewed candidate does not define ENCOUNTER_DATE in ACCESS_LOG; the "
                        "exhaustive ACCESS_LOG column list confirms that the requested column is absent."
                    )
                if rationale is None:
                    rationale = (
                        "Reviewed candidate does not contain evidence needed to answer this "
                        "bounded query."
                    )
                qrel_rows.append(
                    {
                        "query_id": spec.query_id,
                        "doc_id": chunk["doc_id"],
                        "chunk_id": chunk["chunk_id"],
                        "source_path": chunk["source_path"],
                        "heading_path": chunk["heading_path"],
                        "chunk_category": chunk["category"],
                        "relevance": relevance,
                        "rationale": rationale,
                        "candidate_provenance": sorted(pool[chunk_id]),
                    }
                )

        _jsonl(queries_path, query_rows)
        _jsonl(qrels_path, qrel_rows)
        return len(query_rows), len(qrel_rows)
    finally:
        conn.close()


def validate_gold_set(db_path: Path, queries_path: Path, qrels_path: Path) -> None:
    queries = [
        json.loads(line)
        for line in queries_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    qrels = [
        json.loads(line)
        for line in qrels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    query_by_id = {row["query_id"]: row for row in queries}
    if len(query_by_id) != len(queries):
        raise RuntimeError("duplicate query_id in retrieval query set")

    conn = sqlite3.connect(db_path)
    try:
        index_version = conn.execute(
            "SELECT value FROM index_metadata WHERE key = 'index_version'"
        ).fetchone()[0]
        seen: set[tuple[str, str]] = set()
        positive_counts = {query_id: 0 for query_id in query_by_id}
        for qrel in qrels:
            query_id = qrel["query_id"]
            if query_id not in query_by_id:
                raise RuntimeError(f"qrel references unknown query {query_id}")
            key = (query_id, qrel["chunk_id"])
            if key in seen:
                raise RuntimeError(f"duplicate qrel {key}")
            seen.add(key)
            if qrel["relevance"] not in {0, 1, 2, 3}:
                raise RuntimeError(f"invalid relevance grade for {key}")
            actual = conn.execute(
                "SELECT doc_id FROM chunks WHERE chunk_id = ?", (qrel["chunk_id"],)
            ).fetchone()
            if actual is None or actual[0] != qrel["doc_id"]:
                raise RuntimeError(f"qrel does not match index for {key}")
            if qrel["relevance"] > 0:
                positive_counts[query_id] += 1

        for query_id, query in query_by_id.items():
            if query["index_version"] != index_version:
                raise RuntimeError(f"{query_id} was built against another index version")
            if query["answerable"] != (positive_counts[query_id] > 0):
                raise RuntimeError(f"{query_id} answerability disagrees with its qrels")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate the initial retrieval gold set")
    parser.add_argument("--db", type=Path, default=Path("var/rag/index.sqlite"))
    parser.add_argument(
        "--queries-out", type=Path, default=Path("evals/retrieval_queries.jsonl")
    )
    parser.add_argument(
        "--qrels-out", type=Path, default=Path("evals/retrieval_qrels.jsonl")
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if not args.validate_only:
        query_count, qrel_count = build_gold_set(args.db, args.queries_out, args.qrels_out)
        print(f"wrote {query_count} queries and {qrel_count} judged candidates")
    validate_gold_set(args.db, args.queries_out, args.qrels_out)
    print("retrieval gold set validation passed")


if __name__ == "__main__":
    main()
