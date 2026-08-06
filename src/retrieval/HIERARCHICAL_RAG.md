# Hierarchical RAG Design Proposal

## Implementation status

Phase 1, the storage foundation, is implemented. New indexes store document,
section, and leaf nodes in a parent-linked SQLite `nodes` table. Existing flat
chunks remain unchanged and each leaf links back to its canonical `chunk_id`.
The typed helpers in `retrieval.hierarchy` provide bounded `get_node`,
`get_parent`, `get_children`, and `get_siblings` operations. A separate,
evidence-backed `table_relationships` graph now supports optional one-hop
expansion from flat BM25 seed documents. Search across hierarchy summaries,
adaptive tree expansion, and agent routing remain later phases; production SQL
planning still uses the existing flat BM25 results until graph expansion is
evaluated independently.

## Objective

Transform the current flat chunk index into a hierarchical RAG index that preserves each document's structure. The agent can begin with relevant nodes and expand upward, downward, or sideways until it has enough evidence to answer or reaches its token, tool-call, or time budget.

```text
Document
└── Section or table
    └── Subsection
        └── Sub-subsection
            └── Leaf content chunks
```

For example:

```text
CLARITY_APPOINTMENT
├── Overview
├── General Information
├── Columns
│   ├── APPT_STATUS_C
│   │   ├── Description
│   │   └── Possible values
│   └── PAT_ENC_CSN_ID
│       ├── Description
│       └── Relationships
└── Related Tables
```

## Node representation

Store every document, section, subsection, and leaf chunk as an individual node. SQLite can represent the tree through a self-referencing `parent_id` relationship.

Example node:

```json
{
  "node_id": "abc123",
  "document_id": "doc456",
  "parent_id": "parent789",
  "depth": 2,
  "position": 4,
  "node_type": "subsection",
  "title": "APPT_STATUS_C",
  "heading_path": [
    "CLARITY_APPOINTMENT",
    "Columns",
    "APPT_STATUS_C"
  ],
  "text": "The appointment status column...",
  "summary": "Definition and possible values for appointment status.",
  "token_count": 143
}
```

Important fields include:

- `document_id`: identifies the root document.
- `parent_id`: points to the node immediately above the current node.
- `depth`: represents the node's generation or level.
- `position`: preserves the original order among siblings.
- `node_type`: distinguishes documents, sections, tables, subsections, and leaves.
- `heading_path`: preserves the node's location and semantic context.
- `text`: contains content directly owned by the node.
- `summary`: provides a compact description of the node and, when appropriate, its descendants.
- `token_count`: supports context-budget enforcement.

## Building the tree

During indexing, the parser would maintain a heading stack:

```text
Document title
    → create depth-0 document node

First section or table
    → create depth-1 node under the document

Subheading inside the section
    → create depth-2 node under the section

Sub-subheading
    → create depth-3 node under the subsection
```

When a heading is encountered:

- If it is at the same level, create a sibling.
- If it is deeper, make it a child of the current node.
- If it is shallower, move up the stack until the appropriate parent is found.

Tables require domain-specific structure. A table could be represented as:

```text
Table section
├── Table description
├── Column A
│   ├── Description
│   └── Metadata
├── Column B
│   ├── Description
│   └── Metadata
└── Relationships
```

If a leaf contains too much content, it can still be divided into bounded chunks. Those chunks should become children of the semantic section instead of unrelated flat records.

## Searchable content at multiple levels

Each node should support two text representations:

- **Direct text:** content belonging only to that node.
- **Subtree summary:** a compact description of the node and its descendants.

For example:

```text
Document node:
"Documentation for CLARITY_APPOINTMENT, including appointment status,
dates, providers, encounter identifiers, and related tables."

Columns node:
"Column definitions for appointment status, encounter identifiers,
providers, departments, and scheduling dates."

APPT_STATUS_C leaf:
"Stores the appointment status category value..."
```

This supports both coarse-to-fine retrieval:

```text
Search document summaries
→ choose relevant documents
→ search their section summaries
→ choose relevant sections
→ inspect leaf content
```

and fine-to-coarse retrieval:

```text
Search leaf nodes
→ find the best specific passage
→ add its parent for context
→ add another ancestor only if needed
```

## Tree-navigation tools

The retrieval MCP API could expose bounded operations such as:

```text
search_nodes(query, depth, top_k)
get_node(node_id)
get_children(node_id)
get_parent(node_id)
get_siblings(node_id)
expand_context(node_id, direction, token_budget)
```

The MCP server, rather than the model, should enforce:

- Valid node identifiers.
- Maximum nodes returned per call.
- Maximum traversal depth.
- Token limits.
- Deduplication.
- Document boundaries.
- Allowed traversal directions.

## Initial retrieval strategy

Starting at one fixed generation based only on the prompt may be brittle because the model can predict the wrong level. A safer initial strategy is to search multiple representations:

- Document and section summaries for broad routing.
- Titles and heading paths for exact table or column identifiers.
- Leaf text for precise evidence.

The prompt can influence the preferred starting depth without making it a hard restriction.

For example:

```text
Prompt: "Which table contains appointment status?"
Likely starting level: document or section summaries
```

```text
Prompt: "What does APPT_STATUS_C value 2 mean?"
Likely starting level: column or leaf nodes
```

## Context expansion

Expansion should not always mean recursively moving upward. Different directions answer different information needs:

- **Parent:** adds the meaning and scope of the current section.
- **Children:** adds details represented by a broad node's summary.
- **Siblings:** adds nearby definitions, table rows, or related fields.
- **Ancestor chain:** adds document-level context.
- **Adjacent nodes:** restores context separated by chunk boundaries.

For a precise leaf match:

```text
Include the leaf
→ include a compact parent summary
→ include an adjacent sibling only when relevant
→ move higher only if context is still missing
```

For a broad document or section match:

```text
Inspect its children
→ rank the children against the question
→ descend toward the most relevant leaves
```

This provides both bottom-up expansion for precise searches and top-down traversal for broad searches.

## Agent decisions

After reviewing the current evidence, the model could return one structured decision per step:

```json
{
  "decision": "expand",
  "reason": "The current node identifies the column but does not define its values.",
  "operation": "get_children",
  "node_id": "abc123"
}
```

When the evidence is sufficient:

```json
{
  "decision": "answer",
  "reason": "The retrieved leaf directly defines the requested field.",
  "supporting_node_ids": ["abc123"]
}
```

Allowed decisions could include:

```text
answer
expand_parent
expand_children
expand_siblings
search_again
insufficient_evidence
```

Explicit decisions make traversal bounded, traceable, and easier to evaluate than unconstrained recursion.

## Budget-controlled traversal

The host should own the limits rather than relying only on the model.

Example budget:

```json
{
  "max_tool_calls": 6,
  "max_nodes": 12,
  "max_depth_changes": 4,
  "max_context_tokens": 6000,
  "timeout_seconds": 15
}
```

The traversal loop would be:

```text
Retrieve initial nodes
→ calculate remaining budget
→ determine whether the evidence is sufficient
→ execute one permitted expansion
→ deduplicate nodes
→ recalculate token cost
→ repeat or stop
```

Stop when:

- The agent determines the evidence is sufficient.
- There is no parent, child, sibling, or adjacent node left to inspect.
- Expansion produces no new information.
- Confidence does not improve.
- The token budget is exhausted.
- The tool-call budget is exhausted.
- The time budget is exhausted.

If the system stops without sufficient evidence, it should report that the indexed evidence was insufficient rather than guessing.

## Avoiding context explosion

Automatically including complete ancestors, children, and siblings could make hierarchical retrieval more expensive than flat retrieval. Context should therefore be assembled selectively:

- Store concise summaries for non-leaf nodes.
- Return full text primarily for selected leaves.
- Send each ancestor only once.
- Deduplicate overlapping text.
- Estimate token cost before expansion.
- Prefer the smallest context addition that resolves the uncertainty.

An assembled context package could look like:

```json
{
  "focus": {
    "node_id": "leaf-1",
    "text": "Full leaf content..."
  },
  "ancestors": [
    {
      "node_id": "section-1",
      "summary": "Compact section context..."
    },
    {
      "node_id": "doc-1",
      "summary": "Compact document context..."
    }
  ],
  "remaining_token_budget": 3100
}
```

## Relationship to BM25 and embeddings

The tree does not require replacing BM25. Separate FTS indexes or weighted fields could cover:

- Document summaries.
- Section summaries.
- Node titles and heading paths.
- Leaf content.

BM25 remains valuable for exact technical identifiers such as table and column names. Embeddings can later be added to the same nodes for semantic matching.

```text
BM25 finds exact identifiers
+
Embeddings find semantically related descriptions
+
Tree traversal obtains surrounding context
```

These components address different problems:

- **BM25:** Which node lexically matches the query?
- **Embeddings:** Which node has similar meaning?
- **Tree traversal:** What surrounding information is required to understand the node?

## Evaluation

Hierarchical retrieval should be evaluated at every stage:

- Was the correct document selected?
- Was the correct section selected?
- Was the correct leaf found?
- How many expansion calls were required?
- How many context tokens were consumed?
- Did expansion improve answerability?
- Did irrelevant parent or sibling content reduce answer quality?

Useful metrics include:

```text
document recall@k
section recall@k
leaf recall@k
final evidence recall
average expansion count
average context tokens
answer accuracy
unsupported-claim rate
latency
```

The main comparison should be:

```text
Flat BM25
vs.
Hierarchical BM25
vs.
Hierarchical hybrid retrieval
```

## Proposed architecture in one sentence

Index the documents as parent-linked semantic nodes, retrieve likely nodes at multiple depths, and let a host-controlled, budget-limited agent traverse upward, downward, or sideways until it has sufficient cited evidence or reaches a hard stopping condition.
