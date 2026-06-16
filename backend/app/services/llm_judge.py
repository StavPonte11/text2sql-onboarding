import logging

import httpx
from langfuse import observe
from pydantic import BaseModel, Field

from app.services.scoring import ExecutionResult, ExpectedShape, JudgeOutput

logger = logging.getLogger(__name__)

# System Instructions defined in docs/prompts/judge_prompt.md
JUDGE_SYSTEM_PROMPT = """You are a calibrated SQL evaluation judge for a regulated intelligence data platform. Your scores are used to decide whether tables are published to production. Be conservative — do not award high scores unless correctness is clearly demonstrated.

You are evaluating a TextToSQL agent's output.

You will receive:
1. The user's original question
2. The expected (reference) SQL written by an expert
3. The agent's generated SQL
4. The actual query result metadata (row count, columns returned)
5. The expected result shape (row_count_range, expected_columns)
6. The table schema context

Your task: Score the agent's output across 4 dimensions.

IMPORTANT RULES:
- You are a SECONDARY signal. Execution failure ALWAYS scores 0 regardless of your scores.
- Be strict. A plausible-looking wrong SQL should score < 0.5 on equivalence.
- Do not give partial credit for hallucinated columns or tables.
- Base your scores on what the SQL ACTUALLY does, not what it looks like it might do.
"""

INPUT_BLOCK_TEMPLATE = """=== EVALUATION INPUT ===

USER QUESTION:
{user_question}

EXPECTED SQL (Expert Reference):
{expected_sql}

AGENT GENERATED SQL:
{generated_sql}

RESULT METADATA:
- Executed successfully: {execution_success}
- Rows returned: {result_row_count}
- Columns returned: {result_columns}

EXPECTED RESULT SHAPE:
- Row count range: [{min_rows}, {max_rows}]
- Expected columns: {expected_columns}

TABLE SCHEMA:
{schema_block}

EXECUTION ERROR (if any):
{error_message}
"""


class ReasoningOutput(BaseModel):
    table_selection: str
    sql_equivalence: str
    result_correctness: str
    hallucination: str


class JudgeStructuredOutput(BaseModel):
    table_selection_correctness: float = Field(..., ge=0.0, le=1.0)
    sql_semantic_equivalence: float = Field(..., ge=0.0, le=1.0)
    result_correctness: float = Field(..., ge=0.0, le=1.0)
    hallucination_detected: bool
    reasoning: ReasoningOutput
    failure_type: str | None = None
    confidence_in_judgment: float = Field(..., ge=0.0, le=1.0)


def build_judge_prompt(
    user_question: str,
    expected_sql: str,
    generated_sql: str,
    execution: ExecutionResult,
    expected_shape: ExpectedShape,
    schema_block: str,
) -> str:
    return INPUT_BLOCK_TEMPLATE.format(
        user_question=user_question,
        expected_sql=expected_sql or "N/A",
        generated_sql=generated_sql,
        execution_success=str(execution.success).lower(),
        result_row_count=execution.row_count,
        result_columns=", ".join(execution.columns) if execution.columns else "None",
        min_rows=expected_shape.row_count_min,
        max_rows=expected_shape.row_count_max,
        expected_columns=", ".join(expected_shape.expected_columns)
        if expected_shape.expected_columns
        else "None",
        schema_block=schema_block,
        error_message=execution.error_message or "None",
    )


@observe(name="evaluate_with_llm")
def evaluate_with_llm(
    user_question: str,
    expected_sql: str,
    generated_sql: str,
    execution: ExecutionResult,
    expected_shape: ExpectedShape,
    schema_block: str,
) -> JudgeOutput:
    """
    Executes the LLM-as-a-judge prompt to evaluate a SQL generation attempt.
    This simulates an external API call to OpenAI/Anthropic using the strict JSON schema.
    """
    user_prompt = build_judge_prompt(
        user_question,
        expected_sql,
        generated_sql,
        execution,
        expected_shape,
        schema_block,
    )

    logger.info(f"LLM Judge evaluating question: {user_question[:50]}...")

    from app.config import settings

    api_key = settings.OPENAI_API_KEY

    try:
        # Real LLM Execution via OpenAI API
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4-turbo",
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                    # Provide strict JSON schema matching the Pydantic models
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()

        result_json = response.json()
        content = result_json["choices"][0]["message"]["content"]

        # Parse into strictly validated model
        parsed_output = JudgeStructuredOutput.model_validate_json(content)

        return JudgeOutput(
            table_selection_correctness=parsed_output.table_selection_correctness,
            sql_semantic_equivalence=parsed_output.sql_semantic_equivalence,
            result_correctness=parsed_output.result_correctness,
            hallucination_detected=parsed_output.hallucination_detected,
            failure_type=parsed_output.failure_type,
            reasoning=parsed_output.reasoning.model_dump(),
            confidence_in_judgment=parsed_output.confidence_in_judgment,
        )

    except Exception as e:
        logger.error(f"LLM Judge API Error: {e!s}")
        return JudgeOutput(
            table_selection_correctness=0.0,
            sql_semantic_equivalence=0.0,
            result_correctness=0.0,
            hallucination_detected=False,
            failure_type="execution_error",
            reasoning={"error": f"LLM Judge API Error: {e!s}"},
            confidence_in_judgment=0.0,
        )
