"""
enrichment_orchestrator.py - Coordinates the Category Enrichment Pipeline.

Extracts filters, searches candidate databases, calls LLM, and transforms SQL AST.
"""

import logging
import re
import json
from typing import Tuple, Optional, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from agent.config import settings
from agent.services.enrichment_models import SQLFilterParams, FilterTransformation, TransformationPlan, AgentSQLTable
from agent.services.filter_extractor import FilterExtractor
from agent.services.hybrid_searcher import HybridSearcher
from agent.services.sql_transformer import SQLTransformer
from agent.llm import get_llm

logger = logging.getLogger(__name__)


def get_orchestrator_llm() -> ChatOpenAI:
    """
    Instantiates ChatOpenAI using values specified in application settings.

    Returns:
        A ChatOpenAI instance.
    """
    return get_llm("refiner")


def parse_transformation_plan(content: str) -> TransformationPlan:
    """
    Extracts and parses JSON string blocks to return a structured TransformationPlan.

    Args:
        content: The raw text response from the LLM.

    Returns:
        The validated TransformationPlan.

    Raises:
        ValueError: If JSON parsing or Pydantic validation fails.
    """
    cleaned_content: str = content.strip()
    
    # 1. Try direct raw JSON parsing
    try:
        data = json.loads(cleaned_content)
        return TransformationPlan(**data)
    except Exception:
        pass
        
    # 2. Try parsing json inside triple backticks
    match = re.search(r"```(?'json')?\s*(\{.*?\})\s*```", cleaned_content, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            return TransformationPlan(**data)
        except Exception:
            pass
            
    # 3. Try parsing any curly braces block { ... }
    match = re.search(r"(\{.*?\})", cleaned_content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            return TransformationPlan(**data)
        except Exception:
            pass
            
    raise ValueError("Failed to parse TransformationPlan JSON from LLM response")


class EnrichmentOrchestrator:
    """
    Main entry point for running the Category Enrichment pipeline workflows.
    """

    @staticmethod
    async def enrich_query(
        user_request: str,
        initial_sql: str,
        schema: Dict[str, Dict[str, str]],
        tables: List[AgentSQLTable]
    ) -> Tuple[str, Optional[TransformationPlan], bool]:
        """
        Coordinates the pipeline execution:
        Extraction -> Hybrid Search -> LLM Selection -> AST Transformation.

        Args:
            user_request: The original natural language request from user.
            initial_sql: The draft SQL statement to enrich.
            schema: Database schema metadata dictionary.
            tables: List of AgentSQLTable schemas.

        Returns:
            A tuple of (refined_sql, transformation_plan, is_enriched).
        """
        try:
            # 1. Extract comparison filters from query AST
            filters: List[SQLFilterParams] = FilterExtractor.extract(initial_sql, schema)
            if not filters:
                logger.info("No query filters extracted. Query enrichment skipped.")
                return initial_sql, None, False
                
            # 2. Retrieve candidates from semantic and lexical workflows
            search_results: Dict[str, List[str]] = await HybridSearcher.search(filters, tables)
            if not search_results:
                logger.info("No categorical candidate values found. Query enrichment skipped.")
                return initial_sql, None, False
                
            # Format candidate pools for prompt presentation
            search_results_formatted: str = ""
            for key, candidates in search_results.items():
                col, val = key.split("#@#")
                matching_filter = next((f for f in filters if f.source_column.lower() == col.lower() and str(f.value) == val), None)
                orig_op = matching_filter.operator if matching_filter else "="
                search_results_formatted += f"Column: {col}\nOriginal Operator: {orig_op}\nOriginal Value: {val}\nCandidates: {json.dumps(candidates)}\n\n"
                
            # 3. Request keeping/replacing decisions from LLM
            from agent.langfuse_client import langfuse_client
            from langchain_core.prompts import ChatPromptTemplate
            
            langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_CATEGORY_ENRICHMENT)
            if langfuse_prompt is None:
                raise RuntimeError(
                    f"Langfuse prompt '{settings.LANGFUSE_PROMPT_CATEGORY_ENRICHMENT}' could not be retrieved."
                )
                
            prompt = ChatPromptTemplate.from_messages(
                langfuse_prompt.get_langchain_prompt()
            )
            prompt_value = await prompt.ainvoke(
                {
                    "schema": json.dumps(schema, indent=2),
                    "user_request": user_request,
                    "initial_sql": initial_sql,
                    "search_results_formatted": search_results_formatted,
                }
            )
            messages = prompt_value.to_messages()
            
            llm: ChatOpenAI = get_orchestrator_llm()
            
            plan: Optional[TransformationPlan] = None
            try:
                structured_llm = llm.with_structured_output(TransformationPlan, method="json_schema")
                plan = await structured_llm.ainvoke(messages)
            except Exception as e:
                logger.warning(f"LangChain structured output failed: {e}. Attempting fallback parsing.")
                raw_response = await llm.ainvoke(messages)
                plan = parse_transformation_plan(raw_response.content)
                
            if not plan or not plan.enrichment_details:
                logger.warning("No enrichment mapping details proposed by LLM.")
                return initial_sql, None, False
                
            # Log plan detail
            logger.info(f"LLM Enrichment Transformation Plan: {plan.model_dump_json(indent=2)}")
            
            # Validate and check for ghost value mappings
            for tf in plan.enrichment_details:
                if tf.changed_filter:
                    key: str = f"{tf.column.lower()}#@#{tf.original_value}"
                    candidates: Optional[List[str]] = search_results.get(key)
                    if candidates is None:
                        for k, v in search_results.items():
                            k_col, k_val = k.split("#@#")
                            if k_col == tf.column.lower():
                                candidates = v
                                break
                    if candidates is not None:
                        for ref_val in tf.refined_values:
                            if ref_val not in candidates:
                                logger.warning(
                                    f"[Validation Failure] Ghost value detected: refined value '{ref_val}' "
                                    f"does not exist in candidates list {candidates} for column '{tf.column}'."
                                )
                    else:
                        logger.warning(f"[Validation Failure] No candidate pool found for column '{tf.column}'.")
            
            # 4. Transform predicates inside SQL AST
            refined_sql: str = SQLTransformer.apply(initial_sql, plan)
            
            logger.info(f"Enriched Refined SQL: {refined_sql}")
            is_enriched: bool = any(tf.changed_filter for tf in plan.enrichment_details)
            
            return refined_sql, plan, is_enriched
            
        except Exception as e:
            logger.error(f"Error during Enrichment Orchestration: {e}", exc_info=True)
            return initial_sql, None, False
