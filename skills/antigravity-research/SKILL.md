---
name: antigravity-research
description: Use when researching current external information with the Antigravity MCP server, including web search and reading source URLs.
---

# Antigravity Research

Use the Antigravity MCP server for current web research when it is installed and the user or the applicable `AGENTS.md` has selected it for research. This skill does not change how unrelated tools are used.

## Research Workflow

1. For a web search, call `mcp__antigravity__antigravity_grounded_search` with a focused query. For a known source URL, call `mcp__antigravity__antigravity_fetch_url` with the specific question you need answered.
2. Treat model-generated summaries as leads. Check material claims against the cited primary sources and disclose any gaps or substitutions.
3. Reuse results already obtained. Escalate to dialogue or deep research only when the question needs it.
4. If the corresponding Antigravity operation errors, returns `FALLBACK_REQUIRED`, exhausts its timeout, or is unavailable, use the client's available research tools and state the reason for switching.

The MCP server accepts a per-call `timeout` in seconds (default 300). A client-side tool timeout must exceed the requested operation budget. The server currently limits its model selection to Gemini Flash 3.8+; check `antigravity_list_models` if the available model is unclear.

Managed turns can run commands and change files. Invoke them only when the user's request authorizes that work. They are not part of ordinary research.

For query examples and source handling, read [the research playbook](references/research-playbook.md) when needed.
