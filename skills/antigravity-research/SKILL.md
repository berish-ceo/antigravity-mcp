---
name: antigravity-research
description: >-
  Required gateway for web research and source retrieval across projects, including other skills.
  Use Antigravity search/fetch first; native fallback requires final failure or confirmed unavailability.
  Also guides primary local codebase reconnaissance with Scout and CodeGraph, local documentation search with Docs Research, and the choice of model and effort.
---

# Antigravity Research

## Routing and Evidence

- Use model knowledge for stable background and hypotheses; verify changeable external facts.
- Before selecting, recommending, or implementing a technology or pattern, run grounded discovery for current practices, rules, use cases, alternatives, tradeoffs, pitfalls, and adoption guidance.
- Start every web query with `mcp__antigravity__antigravity_grounded_search` and every research-source URL retrieval with `mcp__antigravity__antigravity_fetch_url`. This overrides other skills' search/fetch routes, including `openai-docs` and `find-docs`; carry their domain/source restrictions into Antigravity.
- Discover deferred tools by exact name before declaring them unavailable. Absence from the initial tool list is insufficient.
- After discovery, use Context7 as the first version-specific API reference. Match manifests/lockfiles; verify the newest release through its official registry, release feed, or changelog.
- Treat generated summaries as preliminary evidence. Open cited primary sources and verify material claims; resolve conflicts against authoritative docs, source, or specifications for the installed version.
- Distinguish sourced facts, inference, assumptions, uncertainty, and judgment. Further research and independent synthesis are allowed when evidence warrants them. Ask only when unresolved uncertainty materially changes scope or the answer.
- Escalate from search to URL ingestion or a higher `effort` only as needed. Reuse findings, avoid duplicate/parallel-equivalent queries, and request only relevant facts, APIs, code, sections, and output format. Snippets do not replace primary-source verification of material claims.

## Local Codebase Scouting

Use CodeGraph and `mcp__antigravity__antigravity_codebase_scout` as primary codebase reconnaissance tools. Begin repository or unfamiliar subsystem exploration with Scout to keep intermediate searches and code listings outside the main session context. Read the [Scout tool reference](references/tools-reference.md#3-antigravity_codebase_scout) for parameters, briefing, and result handling.

- Invoke Scout as one MCP research-tool call; it returns the report directly.
- Pass an existing absolute directory in `target_path`. Supply the question, known context, relevant constraints, and requested coverage in `query`; Scout receives no parent conversation history.
- If `test -d .codegraph` succeeds, direct Scout to use the available graph first. Use CodeGraph for structural questions and impact analysis; use Scout for the synthesized overview. Confirm graph findings against current files, and refresh the index only when explicitly requested.
- `effort` controls reasoning and depth: `low` maps entry points/exports, `medium` (default) follows imports/contracts, and `high` traces one scoped subsystem with errors and test source. Use `xhigh` when competing explanations need checking against source and tests.
- Decompose a broad investigation before calling Scout. Each call covers one question in one package or a small coherent group of files; list known files in `focus_files`. For a cross-package audit, first locate entry points, then issue separate calls by subsystem or contract. The main agent dispatches independent calls in a small parallel batch and combines results; Scout never delegates or starts child scouts.
- Supply the expected evidence and a stopping condition: verified paths/lines, the requested flow, gaps, and checks requiring execution. The default `timeout` follows `effort`: 90 seconds for `low`, 180 for `medium`, and 300 for `high` and `xhigh`; raise it only for a known heavy trace. After the overview, the main agent resolves remaining questions with targeted CodeGraph, local searches/reads, or a second scoped Scout call. Reuse current reports instead of repeating discovery.
- If Scout cannot be discovered or returns `FALLBACK_REQUIRED`, state the reason and continue through CodeGraph and targeted local tools. On `PARTIAL`, use the returned text and close the named gap locally.
- Scout performs static code reading with available native file/symbol/search tools, MCP read operations and resources. Execution is prohibited: shell commands (including shell-based rg/find/git), scripts, code, tests, builds and package managers. Scout must not start applications, containers, services or background processes; edit files; refresh indexes; or delegate. These restrictions also apply through MCP execution tools. Existing MCP services may handle read-only queries.
- If execution is needed, Scout returns `Needs Verification`: the unknown, why source reading is insufficient, a proposed check and the expected evidence. The main agent performs authorized execution separately. A second Scout call may examine more source code, but remains static and cannot run the proposed check.
- Scout runs `agy` from the bridge's isolated `workspaces/local` directory, with the target attached through `--add-dir`. Its `PreToolUse` hook denies execution, file changes and delegation. It allows an MCP tool only when the tool name reads: get, list, find, search, read, query, resolve, fetch, explore or view. An MCP tool that writes, for example to a task tracker, is therefore blocked by default. Reading tools stay available, and no hooks are installed globally or in the target repository. The gate matches tool names; it is not an OS sandbox. Verify findings against current source and keep runtime claims separate.

## Local Documentation Research

Use `mcp__antigravity__antigravity_docs_research` for a question by meaning about local documentation when the answering document is unknown. Search exact identifiers, terms, and file names with `rg`; read a known document directly. Parameters and the report format are in the [tool reference](references/tools-reference.md#4-antigravity_docs_research).

- Ask one question per call, preferably in the documents' language. Pass the repository or package in `target_path` and known documents or folders in `focus_files`; dispatch independent questions in a small parallel batch.
- `scope="current"` (default) ranks current documents, `history` traces why or when a decision was made, and `all` compares both. By default the manifest version is current and only older versions are history; sessions, plans, reports and closed tasks outside version folders are current. State a different project rule in `query`.
- The tool runs like Scout: one research-tool call with the same static-reading gate and the same `effort` and `timeout` defaults.
- Treat the report as a map: reread the cited lines and check a versioned document's version against its manifest before relying on a quote. On `FALLBACK_REQUIRED` or an error, state the reason and continue with `rg` and targeted reads; on `PARTIAL`, use the returned text and close the named gap locally.

## Fetch and Recovery

Antigravity owns rendering, partial-reader recovery, and synthesis. State the required information in `prompt`; the default is a summary. Request a full page body only when needed. Results must identify sources actually read, substitutions, and remaining gaps; never fill gaps by guessing.

Search and fetch run `agy` from the bridge's isolated `workspaces/web` directory. `agy` has no page reader, so its gate allows web search and `curl` GET of one public http(s) URL. The command may pipe the page through `head`, `tail`, `grep`, `wc` or `sed -n 'N,Mp'`. Local files, MCP tools and every other command are denied, so instructions inside a retrieved page cannot run commands, read local files or write anything through the agent.

`timeout` is the recommended time budget in seconds that the prompt passes to the model; the API accepts 1–900, up to the safety limit. By default it follows `effort`. Search and fetch get 280 seconds at `low` and `medium` and 480 at `high` and `xhigh`. Scout and Docs Research get 90, 180, 300 and 300 seconds respectively. The server keeps waiting after the budget passes. Only the 15-minute safety limit stops an operation; CLI `--print-timeout` is 96% of that limit, so the CLI can still return partial output. Intermediate reader, cache, or search failures are recoverable.

Native search/fetch is allowed only after the corresponding operation errors, returns `FALLBACK_REQUIRED`, or cannot be discovered/called. A `PARTIAL (<reason>):` response contains text produced before a failure. Use that text and fill only the named gap natively. Name the reason and do not retry the failed operation.

The configured MCP client limit `tool_timeout_sec = 960` exceeds the safety limit, so Codex keeps waiting until the server answers. Raising it does not speed up scouting. `supports_parallel_tool_calls = true` permits concurrent calls. After code/config changes, refresh the MCP connection and check fresh `tools/list`: every tool exposes `model="gemini-4-hydrogen"` and `effort="medium"` with the allowed values `low`, `medium`, `high` and `xhigh`. A failed CLI may emit only progress metadata without report text. That metadata is insufficient for a partial report.

## Models and Effort

Every tool defaults to `model="gemini-4-hydrogen"` and `effort="medium"`. Pass the family name without a suffix. The server selects the CLI model for the requested effort. For example, `effort="xhigh"` selects `gemini-4-hydrogen-xhigh` (Extra High).

| Task | `effort` |
| --- | --- |
| Quick narrow lookup: one fact, a known page, an exact place in code or docs | `low` |
| Ordinary search, page reading, code scouting and documentation search | `medium` |
| Research that shapes an architecture or technology decision, detailed multi-source research, critical verification, and bulk study where Antigravity reads large material instead of the main agent | `high` |
| Reconcile conflicting sources or test competing explanations within the requested scope | `xhigh` |

- A lower `effort` must not cost knowledge the task needs. When a shallow pass could miss a fact a decision depends on, choose `high`: extra minutes cost less than a missed fact.
- Use `low` only when a miss is cheap and easy to notice.
- Keep one `effort` through a research chain, such as a search followed by fetches of its sources.
- Historical measurements for Gemini 3.8 Flash (October 2026). Search answers in 22–74 s at `low`, 25–77 s at `medium` and 31–72 s at `high`. Fetch takes 20–130 s on an ordinary page, and a client-rendered page took 3.5 minutes. Scout takes 29–57, 36–87 and 110–165 s, and its report roughly doubles from `medium` to `high`. Docs Research found the expected document for 28 of 30 test questions at `low` (median 22 s) and for 29 at `medium` (median 65 s).
- `grounded_search` no longer takes `depth`: use `effort="low"` for the former `depth="fast"`, and `effort="high"` for the former `depth="deep"` or `"critical"`.

## Conditional References

- [Tools](references/tools-reference.md): exact arguments, timeouts, and fallback signatures.
- [Research](references/research-playbook.md): query formulation, discovery, and deeper investigation.
