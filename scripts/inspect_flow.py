"""
inspect_flow.py
===============
Interactive CLI tool to inspect the step-by-step execution flow of the Text2SQL Agent.

Usage:
    uv run python scripts/inspect_flow.py "Show all flights landing today"
    uv run python scripts/inspect_flow.py --interactive
"""

import sys
import os
import asyncio
import argparse
from typing import Any

import warnings
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass
warnings.filterwarnings("ignore", category=UserWarning)

from agent.graph import agent_graph
from agent.langfuse_client import langfuse_client
from langchain_core.messages import HumanMessage
try:
    from langfuse.decorators import observe
except ImportError:
    def observe(*args, **kwargs):
        def decorator(f):
            return f
        return decorator


# ANSI Colors for beautiful terminal output
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_banner(text: str, color: str = CYAN):
    print(f"\n{color}{BOLD}{'='*80}{RESET}")
    print(f"{color}{BOLD} {text}{RESET}")
    print(f"{color}{BOLD}{'='*80}{RESET}\n")


def print_node_header(node_name: str):
    icons = {
        "init_flags": "🚩",
        "validate_config": "⚙️",
        "init_skills": "🧠",
        "extractor": "🗺️",
        "schema_explorer": "📚",
        "detect_ambiguity": "⚖️",
        "ambiguity_resolution": "❓",
        "query_builder": "✍️",
        "hitl_query_approval": "👤",
        "refiner_subagent": "🔄",
        "finalizer": "🏁",
    }
    icon = icons.get(node_name, "▶")
    print(f"\n{MAGENTA}{BOLD}{icon} [NODE: {node_name.upper()}]{RESET}")


def print_refiner_step_header(step_name: str, detail: str = ""):
    icons = {
        "enrich_context": "🔍",
        "agent": "🤖",
        "trino_exec": "⚡",
        "end_success": "✅",
        "end_fail": "❌",
    }
    icon = icons.get(step_name, "🔄")
    title = f"{icon} [REFINER SUBAGENT: {step_name.upper()}"
    if detail:
        title += f" — {detail}"
    title += "]"
    print(f"\n  {CYAN}{BOLD}{title}{RESET}")


@observe(name="inspect_flow_run")
async def run_flow(query: str, auto_approve: bool = True):
    print_banner(f"Running Query Flow: \"{query}\"")

    initial_state = {
        "user_query": query,
        "messages": [HumanMessage(content=query)],
        "non_interactive": auto_approve,
        "execution_path": [],
        "query_enrichments": [],
        "jeen_catalog": "",
        "sql_query": "",
        "trino_error": None,
        "refinement_count": 0,
        "raw_data_ref": None,
        "summary": "",
        "sql_explanation": "",
        "allowed_tables": None,
        "allowed_statuses": None,
        "feedback": None,
        "feedback_route": None,
        "active_extractors": None,
        "active_skills": None,
        "loaded_skills": None,
        "last_error": None,
        "esca_write_failed": None,
        "inline_result_rows": None,
        "inline_result_columns": None,
        "error_history": [],
        "schema_explorer_retry_count": 0,
        "escalated": None,
        "escalation_reason": None,
        "satisfaction_failures": None,
        "satisfaction_fail_count": 0,
        "execution_mode": "standard",
        "runtime_flags": {},
        "locations_dict": None,
        "location_wkt_instruction": None,
        "is_satisfied": None,
        "last_result_data": None,
        "ambiguity_result": None,
        "ambiguity_type": None,
        "clarifying_questions": None,
        "failure_reason": None,
        "ambiguity_retry_count": 0,
    }

    import uuid
    config = {"configurable": {"thread_id": f"cli_session_{uuid.uuid4().hex[:8]}"}}

    print(f"{YELLOW}Streaming graph events...{RESET}\n")

    refiner_started = False
    last_trino_error = None
    last_trino_row_count = None

    try:
        success = True
        async for chunk in agent_graph.astream(
            initial_state, config=config, stream_mode="updates", subgraphs=True
        ):
            namespace, node_dict = chunk

            for node_name, updates in node_dict.items():
                is_subgraph = bool(namespace and len(namespace) > 0)

                if is_subgraph:
                    if not refiner_started:
                        print_node_header("refiner_subagent")
                        refiner_started = True

                    # ── Refiner Subagent Iterations & Events ──
                    if node_name == "agent":
                        count = updates.get("refinement_count", 1)
                        is_sat = updates.get("is_satisfied", False)
                        sql = updates.get("sql_query", "")
                        explanation = updates.get("sql_explanation", "")

                        if count == 1:
                            print_refiner_step_header("VERIFICATION", "Post-Execution Result Verification & Semantic Alignment")
                        else:
                            print_refiner_step_header("VERIFICATION", f"Post-Execution Result Verification (Iteration #{count})")

                        if last_trino_error:
                            lines = [line.strip() for line in str(last_trino_error).splitlines() if line.strip()]
                            first_line_err = lines[0] if lines else "Unknown error"
                            print(f"    {YELLOW}• Trigger:{RESET} ❌ Self-correcting previous database error ({first_line_err})")
                        elif last_trino_row_count == 0:
                            print(f"    {YELLOW}• Trigger:{RESET} ⚠️ Previous query returned 0 rows — adjusting filters/clauses to match data")
                        elif last_trino_row_count is not None and last_trino_row_count > 0:
                            print(f"    {CYAN}• Trigger:{RESET} ✓ Previous query returned {last_trino_row_count} rows — evaluating semantic alignment")

                        if sql:
                            print(f"    {GREEN}• Candidate SQL:{RESET}\n    {BOLD}{sql.replace(chr(10), chr(10) + '    ')}{RESET}")

                        if is_sat:
                            print(f"    {BOLD}• Status:{RESET} {GREEN}✓ Satisfied (Candidate query verified){RESET}")
                            if explanation:
                                print(f"    {CYAN}• Hebrew Translation / Explanation:{RESET}\n    {explanation.replace(chr(10), chr(10) + '    ')}")
                        else:
                            print(f"    {BOLD}• Status:{RESET} {YELLOW}Not Yet Satisfied (Dispatching revised query to Trino){RESET}")

                    elif node_name == "trino_exec":
                        err = updates.get("trino_error")
                        rows = updates.get("inline_result_rows")
                        cols = updates.get("inline_result_columns")
                        sql = updates.get("sql_query", "")
                        last_trino_error = err
                        last_trino_row_count = len(rows) if rows is not None else (0 if not err else None)

                        print_refiner_step_header("TRINO EXECUTION", "Running Query Against Database")
                        if sql:
                            print(f"    {CYAN}Executed SQL:{RESET}\n    {BOLD}{sql.replace(chr(10), chr(10) + '    ')}{RESET}")
                        if err:
                            print(f"    {RED}{BOLD}❌ Trino Execution Error:{RESET}\n    {RED}{err}{RESET}")
                        else:
                            row_count = len(rows) if rows is not None else 0
                            print(f"    {GREEN}{BOLD}✓ Trino Succeeded ({row_count} rows returned){RESET}")
                            if cols:
                                print(f"    Columns: {', '.join(cols)}")
                            if rows and len(rows) > 0:
                                print(f"    Sample Row: {rows[0]}")

                    elif node_name == "enrich_context":
                        sql = updates.get("sql_query", "")
                        print_refiner_step_header("ENRICH CONTEXT", "Context & Category Enrichment")
                        if sql:
                            print(f"    {CYAN}Current Candidate SQL:{RESET}\n    {BOLD}{sql.replace(chr(10), chr(10) + '    ')}{RESET}")

                    elif node_name == "end_success":
                        print_refiner_step_header("END SUCCESS", f"{GREEN}Query Satisfied & Verified{RESET}")

                    elif node_name == "end_fail":
                        reason = updates.get("escalation_reason", "Refinement limit reached")
                        print_refiner_step_header("END FAIL", f"{RED}Refinement Exited ({reason}){RESET}")

                    else:
                        print_refiner_step_header(node_name)

                else:
                    # ── Top-Level Nodes ──
                    if node_name != "refiner_subagent":
                        print_node_header(node_name)

                    if node_name == "extractor":
                        enrichments = updates.get("query_enrichments") or []
                        loc_inst = updates.get("location_wkt_instruction")
                        loc_dict = updates.get("locations_dict")

                        if enrichments:
                            print(f"{GREEN}✓ Extracted Query Enrichments ({len(enrichments)} entries):{RESET}")
                            for item in enrichments:
                                if isinstance(item, dict):
                                    term = item.get("term", "")
                                    ctx = item.get("context", "")
                                    print(f"    • {CYAN}{term}:{RESET} {ctx}")
                                else:
                                    print(f"    • {item}")
                        else:
                            print(f"{YELLOW}• No general enrichments extracted.{RESET}")

                        if loc_inst:
                            print(f"\n  {CYAN}Location WKT Instruction:{RESET}\n    {loc_inst.strip()}")
                        if loc_dict and isinstance(loc_dict, dict) and "coords" in loc_dict:
                            print(f"\n  {CYAN}Location Coordinates & Placeholders:{RESET}")
                            for placeholder, wkt in loc_dict["coords"].items():
                                wkt_preview = wkt[:80] + "..." if len(wkt) > 80 else wkt
                                print(f"    • @{placeholder}@ -> {wkt_preview}")

                    elif node_name == "schema_explorer":
                        catalog = updates.get("jeen_catalog", "")
                        print(f"{GREEN}✓ Jeen Catalog Fetched ({len(catalog)} characters){RESET}")
                        lines = catalog.strip().split("\n")
                        preview = "\n".join(lines[:10])
                        print(f"{CYAN}Catalog Preview:{RESET}\n{preview}")
                        if len(lines) > 10:
                            print(f"{CYAN}... ({len(lines)-10} more lines){RESET}")

                    elif node_name == "detect_ambiguity":
                        amb_type = updates.get("ambiguity_type")
                        color = GREEN if amb_type == "clear" else YELLOW if amb_type == "ambiguous" else RED
                        print(f"Ambiguity Status: {color}{BOLD}{amb_type}{RESET}")
                        if updates.get("clarifying_questions"):
                            print(f"Clarification: {updates.get('clarifying_questions')}")

                    elif node_name == "query_builder":
                        sql = updates.get("sql_query", "")
                        explanation = updates.get("sql_explanation", "")
                        print(f"{GREEN}Initial Generated SQL:{RESET}")
                        print(f"{BOLD}{sql}{RESET}")
                        if explanation:
                            print(f"{CYAN}Explanation:{RESET} {explanation}")

                    elif node_name == "finalizer":
                        summary = updates.get("summary", "")
                        explanation = updates.get("sql_explanation", "")
                        print(f"\n{GREEN}{BOLD}FINAL SUMMARY:{RESET}\n{summary}")
                        if explanation:
                            print(f"\n{CYAN}{BOLD}SQL EXPLANATION:{RESET}\n{explanation}")

                    elif node_name == "__interrupt__":
                        int_val = updates[0].value if isinstance(updates, (list, tuple)) and len(updates) > 0 and hasattr(updates[0], 'value') else updates
                        print(f"  {YELLOW}{BOLD}⚠️ HITL Pause / Escalation Interrupt:{RESET} {int_val}")

                    elif not is_subgraph and node_name == "end_fail":
                        success = False
                        
                    elif node_name not in ("hitl_query_approval", "refiner_subagent", "extractor", "end_fail") and isinstance(updates, dict):
                        # Generic summary of node updates
                        for k, v in updates.items():
                            if k not in ("execution_path", "messages") and v is not None:
                                val_str = str(v)
                                if len(val_str) > 120:
                                    val_str = val_str[:120] + "..."
                                print(f"  • {k}: {val_str}")

        if success:
            print_banner("Execution Completed Successfully!", GREEN)
        else:
            print_banner("Execution Completed with Failure (end_fail)", RED)
            
        return success

    except Exception as exc:
        print_banner(f"Execution Encountered Error: {exc}", RED)
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Inspect Text2SQL agent query flow")
    parser.add_argument("query", nargs="?", default=None, help="The natural language question to ask")
    parser.add_argument("--interactive", action="store_true", help="Interactive prompt mode")
    parser.add_argument("--require-approval", action="store_true", help="Do not auto-approve HITL")
    args = parser.parse_args()

    if args.query:
        success = asyncio.run(run_flow(args.query, auto_approve=not args.require_approval))
        if not success and not args.interactive:
            import sys
            sys.exit(1)
    elif args.interactive or not args.query:
        print_banner("Text2SQL Interactive Flow Inspector", CYAN)
        while True:
            try:
                q = input(f"\n{BOLD}Enter query (or 'exit' to quit): {RESET}").strip()
                if not q or q.lower() in ("exit", "quit", "q"):
                    break
                asyncio.run(run_flow(q, auto_approve=not args.require_approval))
            except (KeyboardInterrupt, EOFError):
                break


if __name__ == "__main__":
    main()
