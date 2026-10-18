"""
Planner Agent: Task Decomposition

This agent decomposes high-level user requests into atomic, testable subtasks
organized as a Directed Acyclic Graph (DAG).
"""

import json
import uuid
import logging

from src.core.schemas import (
    TaskNode,
    TaskGraph,
    TaskState,
    DecompositionRequest,
    DecompositionResponse,
)
from src.utils.json_extract import JSONExtractionError, extract_json
from src.utils.llm_client import LLMClientProtocol
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

    def __init__(self, llm_client: LLMClientProtocol):
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
                agent=self.agent_id,
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
        Extract the JSON array of tasks from an LLM response.

        Uses ``json.JSONDecoder.raw_decode`` scanning (see
        ``src.utils.json_extract``) so prose, markdown fences and nested
        brackets are handled. A ``{"tasks": [...]}`` wrapper is also accepted.

        Args:
            text: Raw text from LLM

        Returns:
            Parsed JSON array

        Raises:
            ValueError: If no valid JSON found
        """
        def is_task_list(value) -> bool:
            return bool(value) and all(isinstance(item, dict) for item in value)

        try:
            return extract_json(text, expect=list, validate=is_task_list)
        except JSONExtractionError as list_error:
            try:
                wrapper = extract_json(
                    text,
                    expect=dict,
                    validate=lambda d: isinstance(d.get("tasks"), list)
                    and is_task_list(d["tasks"]),
                )
                return wrapper["tasks"]
            except JSONExtractionError:
                raise ValueError(
                    f"Could not extract valid JSON task list from response: {list_error}"
                )

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
            if not isinstance(task_data, dict) or not str(task_data.get("description", "")).strip():
                raise ValueError(f"Task {i} missing 'description' field")
            if not isinstance(task_data.get("input_schema", {}), dict) or not isinstance(
                task_data.get("output_schema", {}), dict
            ):
                raise ValueError(f"Task {i} schemas must be JSON objects")

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
                dep_indices = task_data["dependencies"] or []
                if not isinstance(dep_indices, list):
                    raise ValueError(f"Task {i} dependencies must be a list")

                # Validate dependency indices
                for dep_idx in dep_indices:
                    if not isinstance(dep_idx, int) or isinstance(dep_idx, bool):
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
            ValueError: If graph contains cycles or unknown dependencies
        """
        graph.validate_acyclic()
        logger.debug("Task graph validated as DAG (no cycles)")
