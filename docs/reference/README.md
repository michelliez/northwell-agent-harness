# Reference & Research Materials

This folder contains background research, context, and reference materials that informed the architecture but are not current instructions.

**Nothing here is current, and nothing here may be cited as a requirement.** These files are not maintained and not reviewed. Several describe architectures this repository no longer has — notably the MCP-service host retired by [ADR 001](../adr/001-graph-trunk-and-mcp-boundary.md), and a mock data catalog that no longer exists. Where a statement here conflicts with `docs/PRD.md`, `docs/RAG.md`, `docs/adr/`, or the code, it is wrong.

Do not update these files. If something in here is still true and worth keeping, move that part into the canonical document that owns the contract.

## Contents

### `ANTHROPIC_EXAMPLES.md`
Examples and reference material from Anthropic documentation. Useful for understanding AI principles and best practices, but not specific to this project.

### `SECURITY_ARCHITECTURE_STRATEGY.md`
Early security architecture research and threat analysis. Documents were created during initial threat modeling but are now superseded by specific ADRs (002-006) and the invariants in CLAUDE.md.

### `northwell-agent-harness-deep-research.md`
Deep research into healthcare data engineering, EHR text-to-SQL, and clinical data governance. Provides context for why this system was built and what similar systems exist in the literature.

### `policy-gate-reports/`
Build narrative and red-team reports for the policy gate, formerly `tests/reports/`. The tests themselves are the current record.

### PDFs and diagrams
`ASENG-*.pdf`, `system_visuals.png`, and `Northwell Agent Harness-*.png` are the July 2026 kickoff brainstorm, technical notes, and visuals — all predating the current architecture. `sec-data.png` and `sec-infra.png` belong to `SECURITY_ARCHITECTURE_STRATEGY.md`.

## Using These Materials

- **For understanding why decisions were made:** See [docs/adr/](../adr/) instead
- **For current requirements:** See [docs/PRD.md](../PRD.md) and [docs/RAG.md](../RAG.md)
- **For roadmap and planning:** See [docs/PLANS.md](../PLANS.md)
- **For historical context and research basis:** Read these files

These materials are kept for reference but should not drive current implementation decisions. Current decisions are documented in ADRs.
