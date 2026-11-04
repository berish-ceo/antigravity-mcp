"""PreToolUse gate for Antigravity research runs; each isolated workspace passes its profile: local or web."""

from ipaddress import ip_address
import json
import re
import shlex
import sys
from urllib.parse import urlsplit

# Name tokens of steps and tools that execute code, change files or state, schedule work, send messages or delegate.
DENIED_TOKENS = {
    "run", "exec", "execute", "execution", "eval", "spawn", "delegate", "subagent", "subagents", "start", "stop",
    "write", "edit", "replace", "overwrite", "patch", "insert", "update", "append", "rename", "move", "copy",
    "mkdir", "delete", "remove", "apply", "create", "kill", "terminate", "schedule", "send", "manage",
}
DENIED_NAMES = {"command_status", "python", "node_repl"}
# An MCP tool runs only when its name reads: other MCP tools may write, for example to an external tracker.
READ_TOKENS = {"get", "list", "find", "search", "read", "query", "resolve", "fetch", "explore", "view"}
LOCAL_REASON = "Antigravity research only reads. Report checks that need execution or changes to the caller."
# Web runs need no local files or MCP tools; agy has no page reader, so pages arrive through curl.
# run_command moves a command to the background after five seconds; manage_task then reads or stops that curl.
WEB_STEPS = {"search_web", "read_url_content", "finish", "manage_task", "command_status"}
WEB_REASON = ("Web research may only search and run curl GET of one http(s) URL, optionally piped through head, tail, "
              "grep, wc or sed -n 'N,Mp'. Local files, MCP tools and other commands are unavailable.")
CURL_FLAGS = {"-s", "-S", "-L", "-f", "-I", "-i", "--silent", "--show-error", "--location", "--fail", "--head",
              "--include", "--compressed"}
CURL_VALUE_FLAGS = {"-A", "--user-agent", "-H", "--header", "-e", "--referer", "-m", "--max-time", "--connect-timeout",
                    "-r", "--range", "--retry"}
GREP_FLAG = re.compile(r"-[iEFonvwxc]+|-[ABCm]\d+|--(ignore-case|only-matching|extended-regexp|fixed-strings|line-number|invert-match|word-regexp|count)")
SED_PRINT = re.compile(r"(\d+|/[^/]*/)(,(\d+|/[^/]*/))?p")


def tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()))


def public_url(arg: str) -> bool:
    """Accept an http(s) URL whose host is a public domain name or a global IP address."""
    if not re.fullmatch(r"https?://\S+", arg):
        return False
    try:
        host = (urlsplit(arg).hostname or "").lower()
        return ip_address(host).is_global
    except ValueError:
        # Names need a letter or punycode TLD, which rejects numeric hosts such as 2130706433 that curl maps to 127.0.0.1.
        return re.fullmatch(r"([\w-]+\.)+([^\W\d_]{2,}|xn--[a-z0-9-]+)", host) is not None and not host.endswith(
            (".local", ".localhost", ".internal"))


def filters_stdin(stage: list[str]) -> bool:
    """Accept head, tail, wc, grep or sed -n stages that read only the piped page and write nothing."""
    name, *args = stage or [""]
    if name in ("head", "tail"):
        return all(re.fullmatch(r"-[nc]|-[nc]?\+?\d+|\+?\d+", arg) for arg in args)
    if name == "wc":
        return all(re.fullmatch(r"-[lcwm]+", arg) for arg in args)
    if name == "sed":
        return len(args) == 2 and args[0] == "-n" and SED_PRINT.fullmatch(args[1]) is not None
    if name != "grep":
        return False
    operands, rest = [], iter(args)
    for arg in rest:
        if arg in ("-A", "-B", "-C", "-m"):
            if not next(rest, "").isdigit():
                return False
        elif not GREP_FLAG.fullmatch(arg):
            operands.append(arg)
    return len(operands) == 1


def reads_page(command: str) -> bool:
    """Accept curl GET of one http(s) URL, optionally piped through filters_stdin stages."""
    # $ and backticks expand inside double quotes, and control characters can end the command early.
    if any(char in "$`" or ord(char) < 32 for char in command):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split, lexer.commenters = True, ""
    try:
        words = list(lexer)
    except ValueError:
        return False
    stages: list[list[str]] = [[]]
    for word in words:
        if word == "|":
            stages.append([])
        elif set(word) <= set("();<>|&"):
            return False
        else:
            stages[-1].append(word)
    (program, *args), filters = stages[0] or [""], stages[1:]
    urls, rest = [], iter(args)
    for arg in rest:
        flag, _, attached = arg.partition("=") if arg.startswith("--") else (arg, "", "")
        if flag in CURL_VALUE_FLAGS:
            # curl reads a header from a file given as @path, which would send local data to the server.
            if (attached or next(rest, "@")).startswith("@"):
                return False
        elif not (arg in CURL_FLAGS or re.fullmatch(r"-[sSLfIi]+", arg)):
            if not public_url(arg):
                return False
            urls.append(arg)
    return program == "curl" and len(urls) == 1 and all(map(filters_stdin, filters))


def decide(payload: dict, profile: str = "web") -> dict:
    call = payload.get("toolCall", {})
    step = str(call.get("name", ""))
    args = call.get("args", {})
    values = {str(key).lower().replace("_", ""): str(value) for key, value in args.items()} if isinstance(args, dict) else {}
    if profile == "web":
        allowed = reads_page(values.get("commandline", "")) if step == "run_command" else step in WEB_STEPS
        return {"decision": "allow"} if allowed else {"decision": "deny", "reason": WEB_REASON}
    # agy reports an MCP call as the step call_mcp_tool with the MCP tool name in its ToolName argument.
    mcp_tool = values.get("toolname") or (step if "mcp" in tokens(step) else None)
    names = [name for name in (step, values.get("name"), mcp_tool) if name]
    if any(tokens(name) & DENIED_TOKENS or name.lower() in DENIED_NAMES for name in names) or (
        mcp_tool is not None and not tokens(mcp_tool) & READ_TOKENS
    ):
        return {"decision": "deny", "reason": LOCAL_REASON}
    return {"decision": "allow"}


if __name__ == "__main__":
    # Without a profile argument the stricter web profile applies.
    print(json.dumps(decide(json.load(sys.stdin), sys.argv[1] if len(sys.argv) > 1 else "web")))
