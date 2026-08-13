import asyncio
import os
import sys
import argparse

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
BOLD = "\033[1m"
RESET = "\033[0m"

import time
import json
from datetime import datetime

async def run_query(agent, question: str):
    print(f"\n{CYAN}{BOLD}{'='*80}{RESET}")
    print(f"{CYAN}{BOLD} Running Query: \"{question}\"{RESET}")
    print(f"{CYAN}{BOLD}{'='*80}{RESET}\n")
    
    config = {"configurable": {"thread_id": "cli_deep_agent"}}
    
    start_time = time.time()
    final_state = None
    
    async for state in agent.astream(
        {"messages": [("user", question)]}, 
        config,
        stream_mode="values"
    ):
        latest_message = state["messages"][-1]
        latest_message.pretty_print()
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
                writer.writerow(["Timestamp", "Execution Time (s)", "Question", "Final SQL", "Final Answer"])
            
            writer.writerow([
                datetime.now().isoformat()[:19].replace("T", " "),
                round(execution_time, 2),
                q_csv,
                sql_csv,
                ans_csv
            ])
            
        sql_status = "Yes" if final_sql else "None"
        print(f"\n{GREEN}{BOLD}[Extraction Log Saved] Execution time: {round(execution_time, 2)}s, Final SQL captured: {sql_status}{RESET}")

async def main():
    parser = argparse.ArgumentParser(description="Interactive CLI tool to test the Deep Agent.")
    parser.add_argument("query", nargs="?", default="", help="The query to run.")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive loop mode.")
    args = parser.parse_args()

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
                
            await run_query(agent, question)
            
    elif args.query:
        await run_query(agent, args.query)
    else:
        print(f"{RED}{BOLD}Error: You must provide a query or run with --interactive{RESET}")
        parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
