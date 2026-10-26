"""
Interactive CLI for running the Deep Agent against Trino data.

Usage:
    uv run deep-agent "כמה טיסות המריאו מצרפת"
    uv run deep-agent --interactive --session-memory
    uv run deep-agent "..." --log-file runs/deep_agent_runs.jsonl
"""

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.messages import BaseMessage

from agent.config import settings
from deep_agent.agent import create_deep_agent
from deep_agent.context import DeepAgentContext

# ANSI Colors for terminal output
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
WHITE = "\033[97m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

_SQL_BLOCK = re.compile(r"```(?:sql)?\n(.*?)\n?```", re.DOTALL | re.IGNORECASE)
_THOUGHT_BLOCK = re.compile(r"<thought>.*?</thought>", re.DOTALL)


@dataclass
class RunRecord:
    """One CLI run, written as a single JSONL line when --log-file is set."""

    timestamp: str
    execution_time_s: float
    thread_id: str
    question: str
    final_sql: str | None
    final_answer: str


def _message_text(msg: BaseMessage) -> str:
    """Plain text of a message whose content may be a str or a list of content blocks."""
    return msg.text if isinstance(msg, BaseMessage) else str(getattr(msg, "content", ""))


def extract_final_sql_and_answer(messages: list[BaseMessage]) -> tuple[str | None, str]:
    """Return (last SQL block, answer text without SQL/thought blocks) from a finished run."""
    if not messages:
        return None, ""

    final_answer = _message_text(messages[-1])
    sql_blocks = _SQL_BLOCK.findall(final_answer)
    final_sql = sql_blocks[-1].strip() if sql_blocks else None

    # Fallback: if missing from final answer, search backward through history
    if not final_sql:
        for msg in reversed(messages):
            fallback_blocks = _SQL_BLOCK.findall(_message_text(msg))
            if fallback_blocks:
                final_sql = fallback_blocks[-1].strip()
                break

    clean_answer = _THOUGHT_BLOCK.sub("", final_answer)
    clean_answer = _SQL_BLOCK.sub("", clean_answer).strip()
    return final_sql, clean_answer


def append_run_record(log_file: Path, record: RunRecord) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def print_formatted_message(msg: BaseMessage) -> None:
    msg_type = getattr(msg, "type", "")

    if msg_type == "human":
        print(f"\n{YELLOW}{BOLD}{'═'*30} 👤 USER MESSAGE {'═'*30}{RESET}\n")
        print(f"{YELLOW}{_message_text(msg)}{RESET}\n")

    elif msg_type == "ai":
        print(f"\n{MAGENTA}{BOLD}{'═'*30} 🤖 AI MESSAGE {'═'*30}{RESET}\n")

        # 1. Print thoughts / text content
        raw_content = msg.content
        if isinstance(raw_content, list):
            for block in raw_content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        print(f"{WHITE}{block.get('text', '')}{RESET}\n")
                    elif block.get("type") in ("thinking", "reasoning"):
                        thought = block.get("thinking") or block.get("reasoning") or ""
                        print(f"{DIM}💭 [Thinking]\n{thought}{RESET}\n")
                else:
                    print(f"{WHITE}{block}{RESET}\n")
        elif isinstance(raw_content, str) and raw_content.strip():
            print(f"{WHITE}{raw_content}{RESET}\n")

        # 2. Print tool calls
        for tc in getattr(msg, "tool_calls", None) or []:
            print(f"{CYAN}{BOLD}⚡ Tool Call: {tc.get('name', 'unknown_tool')}{RESET}")
            tc_args = tc.get("args", {})
            if tc_args:
                formatted_args = json.dumps(tc_args, indent=2, ensure_ascii=False, default=str)
                indented_args = "\n".join("    " + line for line in formatted_args.splitlines())
                print(f"{WHITE}{indented_args}{RESET}\n")

    elif msg_type == "tool":
        tool_name = getattr(msg, "name", "tool") or "tool"
        content = _message_text(msg)
        is_error = getattr(msg, "status", "") == "error" or content.strip().startswith("Error")
        header_color = RED if is_error else BLUE
        icon = "❌" if is_error else "🛠️ "

        print(f"\n{header_color}{BOLD}{'═'*30} {icon} TOOL: {tool_name} {'═'*30}{RESET}\n")

        # Truncate ONLY for get_database_catalog
        lines = content.splitlines()
        if tool_name == "get_database_catalog" and (len(lines) > 30 or len(content) > 1500):
            preview = "\n".join(lines[:30]) if len(lines) > 30 else content[:1500]
            print(f"{WHITE}{preview}{RESET}")
            print(
                f"\n{YELLOW}{BOLD}✂️  [Truncated full schema catalog for display: showing first 30 of "
                f"{len(lines)} lines ({len(content):,} chars total)] ...{RESET}\n"
            )
        else:
            print(f"{WHITE}{content}{RESET}\n")

    else:
        # Fallback for system or unexpected message types
        print(f"\n{DIM}{'═'*30} {msg_type.upper() if msg_type else 'MESSAGE'} {'═'*30}{RESET}\n")
        print(f"{WHITE}{_message_text(msg)}{RESET}\n")


async def run_query(
    agent, question: str, thread_id: str, context: DeepAgentContext, log_file: Path | None = None
) -> None:
    print(f"\n{CYAN}{BOLD}{'='*80}{RESET}")
    print(f"{CYAN}{BOLD} Running Query: \"{question}\"{RESET}")
    print(f"{DIM} Thread: {thread_id}{RESET}")
    print(f"{CYAN}{BOLD}{'='*80}{RESET}\n")

    config = {"configurable": {"thread_id": thread_id}}

    start_time = time.time()
    final_state = None
    printed_count = 0

    async for state in agent.astream(
        {"messages": [("user", question)]}, config, stream_mode="values", context=context
    ):
        messages = state.get("messages", [])
        while printed_count < len(messages):
            print_formatted_message(messages[printed_count])
            printed_count += 1
        final_state = state

    execution_time = round(time.time() - start_time, 2)
    if not final_state or not final_state.get("messages"):
        return

    final_sql, clean_answer = extract_final_sql_and_answer(final_state["messages"])

    if log_file is not None:
        append_run_record(
            log_file,
            RunRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                execution_time_s=execution_time,
                thread_id=thread_id,
                question=question,
                final_sql=final_sql,
                final_answer=clean_answer,
            ),
        )

    print(
        f"\n{GREEN}{BOLD}[Run finished] Execution time: {execution_time}s, "
        f"Final SQL captured: {'Yes' if final_sql else 'None'}{RESET}"
    )
    if final_sql:
        print(f"\n{CYAN}{BOLD}{'='*30} FINAL GENERATED SQL {'='*30}{RESET}")
        print(f"{GREEN}{final_sql}{RESET}")
        print(f"{CYAN}{BOLD}{'='*81}{RESET}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive CLI tool to test the Deep Agent on Trino data.")
    parser.add_argument("query", nargs="?", default="", help="The natural language question to run (Hebrew or English).")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive loop mode.")
    parser.add_argument(
        "--connection-id", "-c", type=int, default=None,
        help="Jeen metadata connection ID (defaults to JEEN_METADATA_CONNECTION_ID from the environment).",
    )
    parser.add_argument(
        "--session-memory", "-m",
        action="store_true",
        help=(
            "Share a single thread across all queries in this session. "
            "The agent remembers prior questions and their outcomes within the session. "
            "Default: each query gets a fresh thread (isolated, no cross-question memory)."
        ),
    )
    parser.add_argument(
        "--log-file", type=Path, default=None,
        help="Append one JSON line per run (question, SQL, answer, timing) to this file.",
    )
    return parser


async def _amain(args: argparse.Namespace) -> None:
    context = DeepAgentContext(connection_id=args.connection_id)
    target_connection = args.connection_id or settings.JEEN_METADATA_CONNECTION_ID

    # Thread ID strategy:
    # - With --session-memory: all queries share one thread → agent sees prior Q&A in context
    # - Without (default): each query gets a unique thread → clean slate, no context bleed
    session_thread_id = f"cli_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def next_thread_id() -> str:
        return session_thread_id if args.session_memory else f"query_{uuid.uuid4().hex[:8]}"

    print(f"\n{MAGENTA}{BOLD}================================================================{RESET}")
    print(f"{MAGENTA}{BOLD}               DEEP AGENT CLI — TEXT2SQL RUNNER                 {RESET}")
    print(f"{MAGENTA}{BOLD}================================================================{RESET}")
    print(f" {CYAN}Target Connection:{RESET} ID {target_connection}")
    print(f" {CYAN}MCP Endpoint:{RESET}      {settings.JEEN_METADATA_MCP_URL}")
    if args.session_memory:
        print(f" {CYAN}Memory mode:{RESET}       Session (queries share context — agent learns within session)")
        print(f" {CYAN}Session thread:{RESET}    {session_thread_id}")
    else:
        print(f" {CYAN}Memory mode:{RESET}       Isolated (each query is independent — no context bleed)")
    print(f"{MAGENTA}{BOLD}================================================================{RESET}\n")

    print(f"{MAGENTA}{BOLD}Initializing Deep Agent...{RESET}")
    agent = create_deep_agent()

    if args.interactive:
        print(f"{GREEN}{BOLD}Starting Interactive Mode. Type 'quit', 'exit', or 'q' to stop.{RESET}")
        while True:
            try:
                question = input(f"\n{YELLOW}{BOLD}[Deep Agent] Enter your query: {RESET}")
            except (KeyboardInterrupt, EOFError):
                break

            if not question.strip():
                continue
            if question.strip().lower() in ("quit", "exit", "q"):
                print(f"{MAGENTA}{BOLD}Goodbye!{RESET}")
                break

            await run_query(agent, question, next_thread_id(), context, args.log_file)

    elif args.query:
        await run_query(agent, args.query, next_thread_id(), context, args.log_file)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.interactive and not args.query:
        print(f"{RED}{BOLD}Error: You must provide a query or run with --interactive{RESET}\n")
        parser.print_help()
        raise SystemExit(2)
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
