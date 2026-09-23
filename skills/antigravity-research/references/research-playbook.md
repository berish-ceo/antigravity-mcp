# Research Playbook

Use this reference when a search needs more precise targeting or a source is difficult to read.

## Focus a Search

Include the subject, version or date when relevant, and the question to answer. Use `domains` when the user names a source or primary documentation is preferable. Avoid repeating an equivalent query when the first result already answers it.

Example:

```json
{
  "query": "NATS JetStream consumer backpressure TypeScript official documentation",
  "domains": ["docs.nats.io"]
}
```

## Read a Source

Pass a precise `prompt` to `antigravity_fetch_url`. Ask for the facts, API, or passage needed rather than the entire page. If the result is incomplete, note the gap; do not infer missing content from a summary.

## Recover from a Final Failure

An intermediate reader error can be recovered within the Antigravity operation's timeout. Switch to another available research tool only after the call fails, returns `FALLBACK_REQUIRED`, reaches its timeout, or the MCP tool cannot be called. Tell the user why the fallback was needed.
