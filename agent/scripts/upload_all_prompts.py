import os
from langfuse import Langfuse

# ==============================================================================
# CONFIGURATION - Set your private Langfuse server credentials here
# ==============================================================================
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_HOST")

if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY or not LANGFUSE_BASE_URL:
    raise RuntimeError("LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST must be set")

def main():
    print(f"Connecting to Langfuse at {LANGFUSE_BASE_URL}...")
    langfuse_client = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_BASE_URL,
    )

    prompts_to_upload = [
        {
            "name": "text2sql/extractor",
            "prompt": [
                {
                    "role": "system",
                    "content": (
                        "You are a query enrichment assistant for a text-to-SQL system.\n\n"
                        "Your job is to read the user's natural-language query and add context that "
                        "makes ambiguous or implicit terms clearer for downstream processing.\n\n"
                        "Add enrichment entries for things like:\n"
                        "  • Abbreviations or acronyms that have a specific meaning "
                        "(e.g. 'MDA' → 'Magen David Adom')\n"
                        "  • Relative time expressions that can be resolved to absolute dates "
                        "(e.g. 'last quarter' → 'Q1 2025, Jan 1 – Mar 31 2025')\n"
                        "  • Ambiguous proper nouns where context helps "
                        "(e.g. 'Jordan' used as a country vs. a person's name)\n"
                        "  • Domain-specific shorthand the downstream system may not know\n\n"
                        "Do NOT try to identify which database table or column to use — that is handled by a "
                        "separate schema exploration phase.\n"
                        "Do NOT add enrichments for terms that are already fully clear from the query.\n"
                        "If the query needs no enrichment, return an empty enrichments list."
                    )
                },
                {
                    "role": "user",
                    "content": "{{user_query}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/schema_explorer",
            "prompt": [
                {
                    "role": "system",
                    "content": (
                        "You are a Schema Explorer sub-agent. Your goal is to identify the most relevant tables "
                        "and inspect their column details to form a query plan for the user's question.\n\n"
                        "Candidate Tables found:\n{{tables_json}}\n\n"
                        "Detailed Profiles for top tables (with Esca Reference IDs):\n{{profiles_json}}\n\n"
                        "## Decision-Making Rules\n\n"
                        "You MUST make all planning decisions autonomously. This includes:\n"
                        "- Join strategy: If multiple tables are needed to answer the query, decide which tables to join and on which keys — do NOT ask the user.\n"
                        "- Column selection: Choose the most appropriate columns yourself.\n"
                        "- Filter strategy: Infer filters from the user's question.\n"
                        "- Table selection when one is clearly more appropriate: Pick the best match and proceed.\n\n"
                        "## When to Flag Ambiguity (ONLY these cases)\n\n"
                        "Set ambiguity_detected=true ONLY IF:\n"
                        "1. Two or more completely independent tables could each independently and fully answer the user's question, "
                        "and you have no way to determine which one the user wants (e.g., 'orders' vs 'orders_archive' with no "
                        "indication of time range, or two fact tables from different business domains that both seem equally relevant).\n"
                        "2. There is no table in the catalog that can answer the query at all.\n\n"
                        "Do NOT flag ambiguity for: join decisions, column choices, filter logic, or any decision you can make yourself.\n\n"
                        "## Output Format\n\n"
                        "Output MUST be a valid JSON object with the following keys:\n"
                        "- schema_plan: detailed query plan describing tables, columns, joins, and Esca reference IDs (empty string if ambiguity_detected is true)\n"
                        "- ambiguity_detected: boolean, true ONLY in the hard-blocker cases described above\n"
                        "- ambiguity_message: a concise question to ask the user to resolve the blocker (empty string if ambiguity_detected is false)\n"
                        "- candidate_options: list of strings (table names or options) for the user to choose from (empty list if ambiguity_detected is false)\n"
                        "Return only the raw JSON, no markdown formatting (no ```json code blocks)."
                    )
                },
                {
                    "role": "user",
                    "content": "{{human_message}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/query_builder",
            "prompt": [
                {
                    "role": "system",
                    "content": "You are a SQL expert who specializes in trino. Build a SQL query based on the plan and user query. Output ONLY the SQL query, nothing else."
                },
                {
                    "role": "user",
                    "content": "Plan: {{schema_plan}}\nQuery: {{user_query}}{{feedback_str}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/refiner",
            "prompt": [
                {
                    "role": "system",
                    "content": "You are a Trino SQL expert. Fix the SQL query based on the database error. Output ONLY the fixed SQL query, nothing else (no backticks, no explanation)."
                },
                {
                    "role": "user",
                    "content": "SQL: {{sql}}\nError: {{error}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/finalizer_summary",
            "prompt": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful data assistant. Summarize the findings for the user nicely. "
                        "You are given the SQL query that was executed and a preview of the queried data (columns and first few rows) to help you understand the context of the results. "
                        "Note: If the columns contain single items or aliases like `_col0` with a numeric value, this is the result of an aggregation query (such as `COUNT(*)`). Use this direct result to answer the user's question.\n\n"
                        "Data Preview:\n{{data_preview}}"
                    )
                },
                {
                    "role": "user",
                    "content": "User asked: {{user_query}}\nSQL Query: {{sql_query}}\nData Ref: {{raw_data_ref}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/finalizer_sql_explanation",
            "prompt": [
                {
                    "role": "system",
                    "content": (
                        "You are a database analyst assistant. Explain the following SQL query in clear, natural language. "
                        "Describe what fields and tables are queried, any filters, joins, groupings, or aggregations, and what the query accomplishes. "
                        "Keep the explanation concise and professional."
                    )
                },
                {
                    "role": "user",
                    "content": "SQL Query:\n{{sql_query}}"
                }
            ],
            "type": "chat"
        },
        {
            "name": "text2sql/rejection_router",
            "prompt": [
                {
                    "role": "system",
                    "content": (
                        "You are a sub-agent routing system. Analyze the user feedback on the previous query builder plan and select which phase to route the execution back to:\n"
                        "- extractor: Choose this if the user is correcting the query intent, entities, constants, adding or mentioning new entities or asking a completely different question.\n"
                        "- schema_explorer: Choose this if the user points out a wrong table, suggests using a different table/view, or references table schema selection issues.\n"
                        "- query_builder: Choose this if the user is requesting a minor change in the SQL query itself (like modifying a WHERE clause, sorting, limits, joins, or specific syntax adjustments), but the tables and overall query intent are correct."
                    )
                },
                {
                    "role": "user",
                    "content": "User Feedback: {{feedback}}"
                }
            ],
            "type": "chat"
        }
    ]

    for p in prompts_to_upload:
        try:
            print(f"Uploading prompt: {p['name']}...")
            langfuse_client.create_prompt(
                name=p["name"],
                prompt=p["prompt"],
                type=p["type"]
            )
            print(f"  ✓ Successfully uploaded {p['name']}")
        except Exception as e:
            print(f"  ✗ Failed to upload {p['name']}: {e}")

if __name__ == "__main__":
    main()
