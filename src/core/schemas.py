"""
Core data schemas for the Autopoietic Agent.

This module defines all Pydantic models used throughout the system.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime


class TaskState(str, Enum):
    """State of a task in the execution pipeline."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"


class TaskNode(BaseModel):
    """
    Represents a single atomic task in the decomposition graph.

    Each task is small enough to be implemented as a single function
    and tested independently.
    """
    id: str = Field(..., description="Unique task identifier")
    description: str = Field(..., description="Human-readable task description")
    state: TaskState = Field(default=TaskState.PENDING, description="Current task state")
    dependencies: List[str] = Field(default_factory=list, description="Task IDs this depends on")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for inputs")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for outputs")
    test_suite_path: Optional[str] = Field(default=None, description="Path to generated test file")
    implementation_path: Optional[str] = Field(default=None, description="Path to implementation file")
    error_trace: Optional[str] = Field(default=None, description="Error message if task failed")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def update_state(self, new_state: TaskState, error: Optional[str] = None) -> None:
        """Update task state and timestamp."""
        self.state = new_state
        self.updated_at = datetime.now()
        if error:
            self.error_trace = error


class TaskGraph(BaseModel):
    """
    Directed Acyclic Graph (DAG) of tasks.

    Manages task dependencies and provides methods for topological operations.
    """
    nodes: List[TaskNode] = Field(default_factory=list, description="All tasks in the graph")

    def add_node(self, node: TaskNode) -> None:
        """Add a task node to the graph."""
        self.nodes.append(node)

    def get_node(self, task_id: str) -> Optional[TaskNode]:
        """Retrieve a task by ID."""
        return next((n for n in self.nodes if n.id == task_id), None)

    def get_ready_tasks(self) -> List[TaskNode]:
        """
        Get tasks that are ready to execute (all dependencies satisfied).

        Returns:
            List of TaskNode objects in PENDING state with all dependencies VERIFIED.
        """
        ready = []
        for node in self.nodes:
            if node.state != TaskState.PENDING:
                continue

            # Check if all dependencies are satisfied
            deps_satisfied = all(
                self.get_node(dep_id) and self.get_node(dep_id).state == TaskState.VERIFIED
                for dep_id in node.dependencies
            )

            if deps_satisfied:
                ready.append(node)

        return ready

    def get_failed_tasks(self) -> List[TaskNode]:
        """Get all tasks in FAILED state."""
        return [n for n in self.nodes if n.state == TaskState.FAILED]

    def is_complete(self) -> bool:
        """Check if all tasks are in a terminal state (VERIFIED or FAILED)."""
        return all(n.state in [TaskState.VERIFIED, TaskState.FAILED] for n in self.nodes)

    def get_completion_rate(self) -> float:
        """Get percentage of tasks that are VERIFIED."""
        if not self.nodes:
            return 0.0
        verified = sum(1 for n in self.nodes if n.state == TaskState.VERIFIED)
        return verified / len(self.nodes)


class TestSuiteMetadata(BaseModel):
    """Metadata about a generated test suite."""
    task_id: str = Field(..., description="Associated task ID")
    test_file_path: str = Field(..., description="Path to pytest file")
    test_functions: List[str] = Field(default_factory=list, description="Names of test functions")
    assertion_count: int = Field(default=0, description="Total assertions in suite")
    assertion_density: float = Field(default=0.0, description="Assertions per line of code")
    coverage_estimate: float = Field(default=0.0, description="Estimated code coverage")
    validation_status: str = Field(default="pending", description="validated, failed_critic, or pending")
    generated_at: datetime = Field(default_factory=datetime.now)


class TestValidationResult(BaseModel):
    """Result of test quality validation by the Critic."""
    valid: bool = Field(..., description="Whether test suite passes quality checks")
    assertion_count: int = Field(..., description="Number of assertions found")
    total_lines: int = Field(..., description="Total lines of test code")
    assertion_density: float = Field(..., description="Ratio of assertions to total lines")
    issues: List[str] = Field(default_factory=list, description="List of quality issues found")
    recommendation: str = Field(..., description="APPROVE, REJECT, or NEEDS_REVISION")
    reason: str = Field(..., description="Explanation of recommendation")


class TestExecutionResult(BaseModel):
    """Result of executing a test suite in the sandbox."""
    task_id: str = Field(..., description="Associated task ID")
    success: bool = Field(..., description="Whether all tests passed")
    exit_code: int = Field(..., description="Process exit code")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    tests_passed: int = Field(default=0, description="Number of tests that passed")
    tests_failed: int = Field(default=0, description="Number of tests that failed")
    execution_time: float = Field(default=0.0, description="Execution time in seconds")
    executed_at: datetime = Field(default_factory=datetime.now)


class ToolMetadata(BaseModel):
    """Metadata for a registered MCP tool."""
    id: str = Field(..., description="Unique tool identifier")
    name: str = Field(..., description="Tool function name")
    description: str = Field(..., description="Tool description from docstring")
    signature: str = Field(..., description="Function signature")
    source_code: str = Field(..., description="Complete source code")
    docstring: str = Field(..., description="Complete docstring")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for inputs")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for outputs")
    test_results: Optional[TestExecutionResult] = Field(default=None, description="Latest test results")
    version: str = Field(default="1.0.0", description="Semantic version")
    status: str = Field(default="active", description="active, broken, or deprecated")
    created_at: datetime = Field(default_factory=datetime.now)
    last_verified_at: Optional[datetime] = Field(default=None)
    usage_count: int = Field(default=0, description="Number of times tool has been called")
    success_rate: float = Field(default=1.0, description="Ratio of successful executions")


class AgentMessage(BaseModel):
    """Message passed between agents via the message bus."""
    event_type: str = Field(..., description="Type of event (e.g., 'task_decomposed')")
    sender: str = Field(..., description="Agent ID that sent the message")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event payload data")
    timestamp: datetime = Field(default_factory=datetime.now)
    correlation_id: str = Field(..., description="ID for tracing related events")


class DecompositionRequest(BaseModel):
    """Request to decompose a user query into tasks."""
    user_request: str = Field(..., description="Natural language user request")
    max_tasks: int = Field(default=10, description="Maximum number of tasks to generate")
    correlation_id: str = Field(..., description="Correlation ID for tracking")


class DecompositionResponse(BaseModel):
    """Response from the Planner agent."""
    task_graph: TaskGraph = Field(..., description="Generated task graph")
    reasoning: str = Field(default="", description="Explanation of decomposition")
    correlation_id: str = Field(..., description="Correlation ID for tracking")
