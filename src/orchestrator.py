"""
Autopoietic Agent: Meta-MCP Orchestrator

Main entry point that coordinates all agents to autonomously generate tools.
"""

import os
import logging
import uuid
from typing import Dict, Any
from dotenv import load_dotenv
from fastmcp import FastMCP

from src.core.schemas import (
    TaskState,
    DecompositionRequest,
)
from src.utils.llm_client import LLMClient
from src.agents.planner import PlannerAgent
from src.agents.auditor import AuditorAgent
from src.agents.architect import ArchitectAgent
from src.agents.warden import WardenAgent

# Load environment variables
load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP(
    name="autopoietic-agent",
    version="0.1.0",
)

# Initialize agents (global scope for MCP tools)
llm_client = LLMClient()
planner = PlannerAgent(llm_client)
auditor = AuditorAgent(llm_client)
architect = ArchitectAgent(llm_client)
warden = WardenAgent()


@mcp.tool()
def health_check() -> dict:
    """
    System health check - verifies the orchestrator is running.

    Returns:
        System status and component information
    """
    return {
        "status": "healthy",
        "version": "0.1.0",
        "phase": "1-foundation",
        "components": {
            "planner": "operational",
            "auditor": "operational",
            "architect": "operational",
            "warden": "operational",
            "librarian": "not_implemented",
            "janitor": "not_implemented",
        },
        "token_usage": llm_client.get_token_usage(),
    }


@mcp.tool()
def generate_tool(description: str, max_tasks: int = 10) -> dict:
    """
    Generate a new MCP tool based on a natural language description.

    This is the main entry point for the autopoietic system. It orchestrates
    the entire pipeline: decomposition → test generation → code synthesis → verification.

    Args:
        description: Natural language description of the desired tool
        max_tasks: Maximum number of subtasks to decompose into

    Returns:
        Status of tool generation process with details
    """
    correlation_id = f"gen_{uuid.uuid4().hex[:8]}"
    logger.info(f"=== Starting tool generation: {correlation_id} ===")
    logger.info(f"Description: {description}")

    try:
        # Phase 1: Decomposition
        logger.info("PHASE 1: Task Decomposition")
        decomposition_request = DecompositionRequest(
            user_request=description,
            max_tasks=max_tasks,
            correlation_id=correlation_id,
        )

        decomposition_response = planner.decompose(decomposition_request)
        task_graph = decomposition_response.task_graph

        logger.info(f"Decomposed into {len(task_graph.nodes)} tasks")

        # For simplicity in Phase 1, we'll process tasks sequentially
        # In later phases, we'll leverage the DAG for parallel execution
        results = []

        for task in task_graph.nodes:
            logger.info(f"\n--- Processing task: {task.id} ---")
            logger.info(f"Description: {task.description}")

            try:
                # Phase 2: Test Generation
                logger.info("PHASE 2: Test Generation")
                task.update_state(TaskState.IN_PROGRESS)

                test_metadata = auditor.generate_test_suite(task)
                test_code = open(test_metadata.test_file_path).read()

                logger.info(
                    f"Generated {len(test_metadata.test_functions)} tests "
                    f"with {test_metadata.assertion_count} assertions"
                )

                # Validate test quality
                validation = auditor.validate_test_quality(test_code)

                if validation.recommendation != "APPROVE":
                    logger.warning(
                        f"Test quality issues: {validation.reason}"
                    )
                    logger.warning(f"Issues: {validation.issues}")
                    # For Phase 1, we'll continue anyway
                    # In Phase 4, we might reject or regenerate

                # Phase 3: Code Synthesis
                logger.info("PHASE 3: Code Synthesis")

                impl_path = architect.synthesize_implementation(task, test_code)
                logger.info(f"Generated implementation: {impl_path}")

                # Phase 4: Test Execution (Iterative Refinement)
                logger.info("PHASE 4: Test Execution & Refinement")

                max_iterations = 3
                for iteration in range(1, max_iterations + 1):
                    logger.info(f"Testing iteration {iteration}/{max_iterations}")

                    test_result = warden.execute_tests(task)

                    if test_result.success:
                        # Tests passed! Mark as verified
                        task.update_state(TaskState.VERIFIED)
                        logger.info(f"✓ Task {task.id} VERIFIED")

                        results.append({
                            "task_id": task.id,
                            "description": task.description,
                            "status": "verified",
                            "implementation_path": task.implementation_path,
                            "test_path": task.test_suite_path,
                            "tests_passed": test_result.tests_passed,
                            "execution_time": test_result.execution_time,
                        })
                        break
                    else:
                        # Tests failed - try to refine
                        if iteration < max_iterations:
                            logger.warning(
                                f"✗ Tests failed ({test_result.tests_failed} failures), "
                                f"attempting refinement..."
                            )

                            architect.refine_implementation(
                                task, test_code, test_result, iteration
                            )
                        else:
                            # Max iterations reached
                            task.update_state(TaskState.FAILED)
                            logger.error(
                                f"✗ Task {task.id} FAILED after {max_iterations} iterations"
                            )

                            results.append({
                                "task_id": task.id,
                                "description": task.description,
                                "status": "failed",
                                "error": f"Tests failed after {max_iterations} refinement attempts",
                                "stderr": test_result.stderr,
                            })
                            break

            except Exception as e:
                logger.error(f"Task {task.id} failed with error: {e}", exc_info=True)
                task.update_state(TaskState.FAILED, error=str(e))

                results.append({
                    "task_id": task.id,
                    "description": task.description,
                    "status": "failed",
                    "error": str(e),
                })

        # Summary
        verified_count = sum(1 for r in results if r["status"] == "verified")
        failed_count = sum(1 for r in results if r["status"] == "failed")

        logger.info(f"\n=== Tool Generation Complete: {correlation_id} ===")
        logger.info(f"Tasks verified: {verified_count}/{len(task_graph.nodes)}")
        logger.info(f"Tasks failed: {failed_count}/{len(task_graph.nodes)}")

        return {
            "status": "completed",
            "correlation_id": correlation_id,
            "description": description,
            "total_tasks": len(task_graph.nodes),
            "verified": verified_count,
            "failed": failed_count,
            "results": results,
            "token_usage": llm_client.get_token_usage(),
        }

    except Exception as e:
        logger.error(f"Tool generation failed: {e}", exc_info=True)
        return {
            "status": "error",
            "correlation_id": correlation_id,
            "description": description,
            "error": str(e),
            "token_usage": llm_client.get_token_usage(),
        }


@mcp.tool()
def list_generated_tools() -> dict:
    """
    List all generated tools in the tools directory.

    Returns:
        List of generated tools with metadata
    """
    from pathlib import Path

    tools_dir = Path("./tools")
    tools = []

    if tools_dir.exists():
        for tool_file in tools_dir.glob("*.py"):
            if tool_file.name != "__init__.py":
                # Read first few lines for docstring
                content = tool_file.read_text()
                lines = content.split('\n')

                # Try to extract function name and docstring
                func_name = None
                for line in lines:
                    if line.strip().startswith('def '):
                        func_name = line.split('(')[0].replace('def ', '').strip()
                        break

                tools.append({
                    "file": tool_file.name,
                    "function_name": func_name,
                    "path": str(tool_file),
                })

    return {
        "total": len(tools),
        "tools": tools,
    }


def main():
    """Main entry point for the MCP server."""
    logger.info("Starting Autopoietic Agent MCP Server")
    logger.info(f"Model: {llm_client.model}")
    mcp.run()


if __name__ == "__main__":
    main()
