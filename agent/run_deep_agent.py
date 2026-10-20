import asyncio
import os
import sys
import argparse
import uuid

# Add the src directory to sys.path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))
# Also add core to the path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../core/src')))

import warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from deep_agent.agent import create_deep_agent

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

import time
import json
from datetime import datetime


def print_formatted_message(msg):
    msg_type = getattr(msg, "type", "")

    if msg_type == "human":
        print(f"\n{YELLOW}{BOLD}{'═'*30} 👤 USER MESSAGE {'═'*30}{RESET}\n")
        content = getattr(msg, "content", "")
        print(f"{YELLOW}{content}{RESET}\n")

    elif msg_type == "ai":
        print(f"\n{MAGENTA}{BOLD}{'═'*30} 🤖 AI MESSAGE {'═'*30}{RESET}\n")

        # 1. Print thoughts / text content
        raw_content = getattr(msg, "content", "")
        if raw_content:
            if isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            print(f"{WHITE}{block.get('text', '')}{RESET}\n")
                        elif block.get("type") == "thinking":
                            print(f"{DIM}💭 [Thinking]\n{block.get('thinking', '')}{RESET}\n")
                    else:
                        print(f"{WHITE}{block}{RESET}\n")
            elif isinstance(raw_content, str) and raw_content.strip():
                print(f"{WHITE}{raw_content}{RESET}\n")

        # 2. Print tool calls
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            tc_name = tc.get("name", "unknown_tool")
            tc_args = tc.get("args", {})
            print(f"{CYAN}{BOLD}⚡ Tool Call: {RESET}{CYAN}{BOLD}{tc_name}{RESET}")
            if tc_args:
                try:
                    formatted_args = json.dumps(tc_args, indent=2, ensure_ascii=False)
                    indented_args = "\n".join("    " + line for line in formatted_args.splitlines())
                    print(f"{WHITE}{indented_args}{RESET}\n")
                except Exception:
                    print(f"{WHITE}    Args: {tc_args}{RESET}\n")

    elif msg_type == "tool":
        tool_name = getattr(msg, "name", "tool") or "tool"
        content = getattr(msg, "content", "")
        is_error = getattr(msg, "status", "") == "error" or (isinstance(content, str) and content.strip().startswith("Error"))
        header_color = RED if is_error else BLUE
        icon = "❌" if is_error else "🛠️ "

        print(f"\n{header_color}{BOLD}{'═'*30} {icon} TOOL: {tool_name} {'═'*30}{RESET}\n")

        # Truncate ONLY for get_database_catalog
        if tool_name == "get_database_catalog" and isinstance(content, str):
            lines = content.splitlines()
            if len(lines) > 30 or len(content) > 1500:
                preview = "\n".join(lines[:30]) if len(lines) > 30 else content[:1500]
                print(f"{WHITE}{preview}{RESET}")
                print(f"\n{YELLOW}{BOLD}✂️  [Truncated full schema catalog for display: showing first 30 of {len(lines)} lines ({len(content):,} chars total)] ...{RESET}\n")
            else:
                print(f"{WHITE}{content}{RESET}\n")
        else:
            # All other tools: print in full
            if isinstance(content, str):
                print(f"{WHITE}{content}{RESET}\n")
            else:
                print(f"{WHITE}{str(content)}{RESET}\n")

    else:
        # Fallback for system or unexpected message types
        print(f"\n{DIM}{'═'*30} {msg_type.upper() if msg_type else 'MESSAGE'} {'═'*30}{RESET}\n")
        content = getattr(msg, "content", "")
        print(f"{WHITE}{content}{RESET}\n")


async def run_query(agent, question: str, thread_id: str):
    print(f"\n{CYAN}{BOLD}{'='*80}{RESET}")
    print(f"{CYAN}{BOLD} Running Query: \"{question}\"{RESET}")
    print(f"{DIM} Thread: {thread_id}{RESET}")
    print(f"{CYAN}{BOLD}{'='*80}{RESET}\n")
    
    config = {"configurable": {"thread_id": thread_id}}
    
    start_time = time.time()
    final_state = None
    printed_count = 0
    
    async for state in agent.astream(
        {"messages": [("user", question)]}, 
        config,
        stream_mode="values"
    ):
        messages = state.get("messages", [])
        while printed_count < len(messages):
            msg = messages[printed_count]
            print_formatted_message(msg)
            printed_count += 1
        final_state = state
        
    execution_time = time.time() - start_time
    
    # Extract execution details
    if final_state and "messages" in final_state:
        messages = final_state["messages"]
        final_answer = messages[-1].content if hasattr(messages[-1], "content") else ""
        
        import re
        # 1. Safely extract the LAST SQL block (in case there are drafts in thoughts)
        sql_blocks = re.findall(r'```(?:sql)?\n(.*?)\n?```', final_answer, re.DOTALL | re.IGNORECASE)
        final_sql = sql_blocks[-1].strip() if sql_blocks else None
        
        # Fallback: If missing from final answer, search backward through history
        if not final_sql:
            for msg in reversed(messages):
                if hasattr(msg, "content") and isinstance(msg.content, str):
                    fallback_blocks = re.findall(r'```(?:sql)?\n(.*?)\n?```', msg.content, re.DOTALL | re.IGNORECASE)
                    if fallback_blocks:
                        final_sql = fallback_blocks[-1].strip()
                        break
        
        # 2. Strip <thought> blocks and ```sql``` blocks from the final answer so it's just text
        clean_answer = re.sub(r'<thought>.*?</thought>', '', final_answer, flags=re.DOTALL)
        clean_answer = re.sub(r'```(?:sql)?\n.*?\n?```', '', clean_answer, flags=re.DOTALL | re.IGNORECASE)
        clean_answer = clean_answer.strip()
                            
        # Log to CSV file
        import csv
        log_file = "deep_agent_logs.csv"
        file_exists = os.path.isfile(log_file)
        
        # 3. Physically compress all multi-line strings into a single line for the CSV
        q_csv = question.replace('\n', '\\n').replace('\r', '')
        sql_csv = final_sql.replace('\n', '\\n').replace('\r', '') if final_sql else "None"
        ans_csv = clean_answer.replace('\n', '\\n').replace('\r', '')
        
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Execution Time (s)", "Thread ID", "Question", "Final SQL", "Final Answer"])
            
            writer.writerow([
                datetime.now().isoformat()[:19].replace("T", " "),
                round(execution_time, 2),
                thread_id,
                q_csv,
                sql_csv,
                ans_csv
            ])
            
        sql_status = "Yes" if final_sql else "None"
        print(f"\n{GREEN}{BOLD}[Extraction Log Saved] Execution time: {round(execution_time, 2)}s, Final SQL captured: {sql_status}{RESET}")
        if final_sql:
            print(f"\n{CYAN}{BOLD}{'='*30} FINAL GENERATED SQL {'='*30}{RESET}")
            print(f"{GREEN}{final_sql}{RESET}")
            print(f"{CYAN}{BOLD}{'='*81}{RESET}\n")


async def main():
    parser = argparse.ArgumentParser(description="Interactive CLI tool to test the Deep Agent on Trino data.")
    parser.add_argument("query", nargs="?", default="", help="The natural language question to run (Hebrew or English).")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive loop mode.")
    parser.add_argument("--connection-id", "-c", type=int, default=None, help="Jeen metadata connection ID (defaults to 89: trino_ingestion / flights).")
    parser.add_argument(
        "--session-memory", "-m",
        action="store_true",
        help=(
            "Share a single thread across all queries in this session. "
            "The agent remembers prior questions and their outcomes within the session. "
            "Default: each query gets a fresh thread (isolated, no cross-question memory)."
        ),
    )
    args = parser.parse_args()

    from agent.utils.jeen_metadata_client import get_jeen_metadata_client
    client = get_jeen_metadata_client()
    if args.connection_id:
        os.environ["JEEN_METADATA_CONNECTION_ID"] = str(args.connection_id)
        client._connection_id = args.connection_id

    # Thread ID strategy:
    # - With --session-memory: all queries share one thread → agent sees prior Q&A in context
    # - Without (default): each query gets a unique thread → clean slate, no context bleed
    session_thread_id = f"cli_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print(f"\n{MAGENTA}{BOLD}================================================================{RESET}")
    print(f"{MAGENTA}{BOLD}               DEEP AGENT CLI — TEXT2SQL RUNNER                 {RESET}")
    print(f"{MAGENTA}{BOLD}================================================================{RESET}")
    print(f" {CYAN}Target Connection:{RESET} ID {client._connection_id} ({'trino_ingestion / flights' if client._connection_id == 89 else 'custom connection'})")
    print(f" {CYAN}MCP Endpoint:{RESET}      {client._mcp_url}")
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
                
            if question.strip().lower() in ('quit', 'exit', 'q'):
                print(f"{MAGENTA}{BOLD}Goodbye!{RESET}")
                break

            if args.session_memory:
                thread_id = session_thread_id
            else:
                thread_id = f"query_{uuid.uuid4().hex[:8]}"

            await run_query(agent, question, thread_id)
            
    elif args.query:
        thread_id = session_thread_id if args.session_memory else f"query_{uuid.uuid4().hex[:8]}"
        await run_query(agent, args.query, thread_id)
    else:
        print(f"{RED}{BOLD}Error: You must provide a query or run with --interactive{RESET}\n")
        parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
