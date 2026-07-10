# Failures for Edge Router LLM -> MCP Tool Call
Goal: identify failure points that can scale to the larger project's edges. Add a guardrail if it can be applied to a later production edge.
Examples:
- Unknown tool name -> unknown retrieval/action type
- Missing query -> Missing required field
- Wrong argument type -> invalid AST field type
- Incorrect JSON format -> Invalid structure LLM Output
- Tool wants broader scope -> Query plan accesses unauthorized columns
- Tool returns error object -> database or retrieval failure
-

wrong tool name
wrong argument name
missing argument
invalid JSON
unnecessary tool call
no tool call when needed
tool input too vague
tool input not normalized