"""
Planner Agent: Task Decomposition

This agent decomposes high-level user requests into atomic, testable subtasks
organized as a Directed Acyclic Graph (DAG).
"""

import json
import uuid
import logging
import re
from typing import Optional

from src.core.schemas import (
    TaskNode,
    TaskGraph,
    TaskState,
    DecompositionRequest,
    DecompositionResponse,
)
from src.utils.llm_client import LLMClient
from src.utils.prompts import (
    PLANNER_SYSTEM,
    PLANNER_DECOMPOSITION_PROMPT,
    format_prompt,
)

logger = logging.getLogger(__name__)


class PlannerAgent:
    """
    Agent responsible for decomposing user requests into task graphs.

    The Planner uses an LLM to break down complex requests into atomic
    subtasks, each small enough to implement as a single function.
    """

    def __init__(self, llm_client: LLMClient):
        """
        Initialize the Planner Agent.

        Args:
            llm_client: LLM client for making API calls
        """
        self.llm = llm_client
        self.agent_id = "planner"

    def decompose(self, request: DecompositionRequest) -> DecompositionResponse:
        """
        Decompose a user request into a task graph.

        Args:
            request: DecompositionRequest containing the user's query

        Returns:
            DecompositionResponse with the generated task graph

        Raises:
            ValueError: If decomposition fails or produces invalid output
        """
        logger.info(f"Decomposing request: {request.user_request[:100]}...")

        # Format the prompt
        prompt = format_prompt(
            PLANNER_DECOMPOSITION_PROMPT,
            user_request=request.user_request,
            max_tasks=request.max_tasks,
        )

        try:
            # Call LLM
            response_text = self.llm.generate(
                prompt=prompt,
                system=PLANNER_SYSTEM,
                max_tokens=4096,
                temperature=0.7,  # Some creativity for decomposition
            )

            # Parse the JSON response
            tasks_data = self._extract_and_parse_json(response_text)

            # Build task graph
            task_graph = self._build_task_graph(tasks_data)

            logger.info(f"Successfully decomposed into {len(task_graph.nodes)} tasks")

            return DecompositionResponse(
                task_graph=task_graph,
                reasoning=f"Decomposed into {len(task_graph.nodes)} atomic tasks",
                correlation_id=request.correlation_id,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response text: {response_text}")
            raise ValueError(f"LLM returned invalid JSON: {e}")

        except Exception as e:
            logger.error(f"Decomposition failed: {e}")
            raise

    def _extract_and_parse_json(self, text: str) -> list:
        """
        Extract and parse JSON array from LLM response.

        The LLM sometimes includes markdown or explanatory text, so we
        need to extract just the JSON portion.

        Args:
            text: Raw text from LLM

        Returns:
            Parsed JSON array

        Raises:
            ValueError: If no valid JSON found
        """
        # Try to find JSON array in the response
        # Look for [ ... ] pattern
        match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)

        if match:
            json_str = match.group(0)
        else:
            # If no match, assume entire response is JSON
            json_str = text.strip()

        try:
            data = json.loads(json_str)

            if not isinstance(data, list):
                raise ValueError("Expected JSON array of tasks")

            return data

        except json.JSONDecodeError:
            # Try to find between code blocks
            code_block_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
            if code_block_match:
                return json.loads(code_block_match.group(1))

            raise ValueError("Could not extract valid JSON from response")

    def _build_task_graph(self, tasks_data: list) -> TaskGraph:
        """
        Build a TaskGraph from parsed JSON data.

        Args:
            tasks_data: List of task dictionaries from LLM

        Returns:
            TaskGraph with all tasks

        Raises:
            ValueError: If task data is invalid
        """
        if not tasks_data:
            raise ValueError("No tasks in decomposition")

        graph = TaskGraph()
        task_id_map = {}  # Map index to task ID

        # Create task nodes
        for i, task_data in enumerate(tasks_data):
            # Validate required fields
            if "description" not in task_data:
                raise ValueError(f"Task {i} missing 'description' field")

            # Generate unique ID
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            task_id_map[i] = task_id

            # Create task node
            node = TaskNode(
                id=task_id,
                description=task_data["description"],
                state=TaskState.PENDING,
                dependencies=[],  # Will be populated next
                input_schema=task_data.get("input_schema", {}),
                output_schema=task_data.get("output_schema", {}),
            )

            graph.add_node(node)

        # Now populate dependencies (second pass to ensure all IDs exist)
        for i, task_data in enumerate(tasks_data):
            node = graph.get_node(task_id_map[i])

            if "dependencies" in task_data:
                dep_indices = task_data["dependencies"]

                # Validate dependency indices
                for dep_idx in dep_indices:
                    if not isinstance(dep_idx, int):
                        raise ValueError(
                            f"Dependency must be integer index, got {dep_idx}"
                        )
                    if dep_idx < 0 or dep_idx >= len(tasks_data):
                        raise ValueError(
                            f"Invalid dependency index {dep_idx} (valid: 0-{len(tasks_data)-1})"
                        )

                # Map indices to task IDs
                node.dependencies = [task_id_map[idx] for idx in dep_indices]

        # Validate DAG (no cycles)
        self._validate_dag(graph)

        return graph

    def _validate_dag(self, graph: TaskGraph) -> None:
        """
        Validate that the task graph is a valid DAG (no cycles).

        Args:
            graph: TaskGraph to validate

        Raises:
            ValueError: If graph contains cycles
        """
        # Use DFS to detect cycles
        visited = set()
        rec_stack = set()

        def has_cycle(node_id: str) -> bool:
            """DFS to detect cycle."""
            visited.add(node_id)
            rec_stack.add(node_id)

            node = graph.get_node(node_id)
            if node:
                for dep_id in node.dependencies:
                    if dep_id not in visited:
                        if has_cycle(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True

            rec_stack.remove(node_id)
            return False

        for node in graph.nodes:
            if node.id not in visited:
                if has_cycle(node.id):
                    raise ValueError(
                        f"Task graph contains cycle involving task: {node.id}"
                    )

        logger.debug("Task graph validated as DAG (no cycles)")
