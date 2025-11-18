"""
Architect Agent: Code Synthesis Engine

This agent generates implementation code that satisfies test suites.
It uses an iterative refinement approach (Reflexion pattern) to fix failures.
"""

import logging
import re
import ast
from pathlib import Path
from typing import Optional

from src.core.schemas import TaskNode, TestExecutionResult
from src.utils.llm_client import LLMClient
from src.utils.prompts import (
    ARCHITECT_SYSTEM,
    ARCHITECT_SYNTHESIS_PROMPT,
    ARCHITECT_REFINER_PROMPT,
    format_prompt,
)

logger = logging.getLogger(__name__)


class ArchitectAgent:
    """
    Agent responsible for generating implementation code.

    The Architect synthesizes Python functions that satisfy test suites,
    using an iterative refinement loop when tests fail.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_output_dir: str = "./tools",
        max_refinement_iterations: int = 3,
    ):
        """
        Initialize the Architect Agent.

        Args:
            llm_client: LLM client for making API calls
            tool_output_dir: Directory to save generated tools
            max_refinement_iterations: Maximum refinement attempts on test failure
        """
        self.llm = llm_client
        self.agent_id = "architect"
        self.tool_output_dir = Path(tool_output_dir)
        self.tool_output_dir.mkdir(parents=True, exist_ok=True)
        self.max_refinement_iterations = max_refinement_iterations

    def synthesize_implementation(
        self, task: TaskNode, test_code: str
    ) -> str:
        """
        Generate implementation code for a task.

        Args:
            task: TaskNode with specification
            test_code: pytest test suite code

        Returns:
            Path to generated implementation file

        Raises:
            ValueError: If code generation fails
        """
        logger.info(f"Synthesizing implementation for task: {task.id}")

        # Derive function name from task description
        function_name = self._derive_function_name(task.description)

        # Format the prompt
        prompt = format_prompt(
            ARCHITECT_SYNTHESIS_PROMPT,
            task_description=task.description,
            function_name=function_name,
            test_code=test_code,
            input_schema=task.input_schema,
            output_schema=task.output_schema,
        )

        try:
            # Call LLM
            code = self.llm.generate(
                prompt=prompt,
                system=ARCHITECT_SYSTEM,
                max_tokens=4096,
                temperature=0.3,  # Low temperature for consistent code
            )

            # Clean up the code
            code = self._clean_code(code)

            # Validate syntax
            self._validate_syntax(code)

            # Save to file
            impl_file_path = self.tool_output_dir / f"{function_name}.py"
            impl_file_path.write_text(code)

            # Update task with implementation path
            task.implementation_path = str(impl_file_path)

            logger.info(f"Generated implementation: {impl_file_path}")

            return str(impl_file_path)

        except Exception as e:
            logger.error(f"Code synthesis failed: {e}")
            raise ValueError(f"Failed to synthesize implementation: {e}")

    def refine_implementation(
        self,
        task: TaskNode,
        test_code: str,
        test_result: TestExecutionResult,
        iteration: int = 1,
    ) -> str:
        """
        Refine implementation based on test failures (Reflexion pattern).

        Args:
            task: TaskNode with original specification
            test_code: pytest test suite code
            test_result: Failed test execution result
            iteration: Current refinement iteration

        Returns:
            Path to refined implementation file

        Raises:
            ValueError: If refinement fails or max iterations reached
        """
        if iteration > self.max_refinement_iterations:
            raise ValueError(
                f"Max refinement iterations ({self.max_refinement_iterations}) reached"
            )

        logger.info(
            f"Refining implementation for task {task.id} (iteration {iteration})"
        )

        # Read original implementation
        if not task.implementation_path:
            raise ValueError("No implementation to refine")

        original_code = Path(task.implementation_path).read_text()

        # Format the refinement prompt
        prompt = format_prompt(
            ARCHITECT_REFINER_PROMPT,
            original_code=original_code,
            test_code=test_code,
            error_trace=test_result.stderr or test_result.stdout,
        )

        try:
            # Call LLM
            refined_code = self.llm.generate(
                prompt=prompt,
                system=ARCHITECT_SYSTEM,
                max_tokens=4096,
                temperature=0.2,  # Even lower for debugging
            )

            # Clean up the code
            refined_code = self._clean_code(refined_code)

            # Validate syntax
            self._validate_syntax(refined_code)

            # Save refined version
            impl_file_path = Path(task.implementation_path)
            impl_file_path.write_text(refined_code)

            logger.info(f"Refined implementation (iteration {iteration})")

            return str(impl_file_path)

        except Exception as e:
            logger.error(f"Code refinement failed: {e}")
            raise ValueError(f"Failed to refine implementation: {e}")

    def _derive_function_name(self, description: str) -> str:
        """
        Derive a snake_case function name from a task description.

        Args:
            description: Task description

        Returns:
            Valid Python function name
        """
        # Convert to lowercase
        name = description.lower()

        # Remove special characters
        name = re.sub(r'[^a-z0-9\s]', '', name)

        # Replace spaces with underscores
        name = re.sub(r'\s+', '_', name)

        # Truncate if too long
        if len(name) > 50:
            name = name[:50]

        # Ensure it starts with a letter
        if name and not name[0].isalpha():
            name = 'func_' + name

        return name or 'generated_function'

    def _clean_code(self, code: str) -> str:
        """
        Clean LLM-generated code.

        Args:
            code: Raw code from LLM

        Returns:
            Cleaned Python code
        """
        # Remove markdown code blocks
        code = re.sub(r'```python\s*', '', code)
        code = re.sub(r'```\s*', '', code)

        # Remove leading/trailing whitespace
        code = code.strip()

        # Ensure it starts with 'def' or 'import'
        lines = code.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('def ') or stripped.startswith('import ') or stripped.startswith('from '):
                start_idx = i
                break

        code = '\n'.join(lines[start_idx:])

        return code

    def _validate_syntax(self, code: str) -> None:
        """
        Validate Python syntax using AST parsing.

        Args:
            code: Python code to validate

        Raises:
            SyntaxError: If code has syntax errors
            ValueError: If code contains dangerous operations
        """
        try:
            # Parse the code
            tree = ast.parse(code)

            # Check for dangerous operations
            for node in ast.walk(tree):
                # Disallow eval, exec, __import__
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        if node.func.id in ['eval', 'exec', '__import__']:
                            raise ValueError(
                                f"Dangerous operation not allowed: {node.func.id}"
                            )

            logger.debug("Code syntax validated successfully")

        except SyntaxError as e:
            logger.error(f"Syntax error in generated code: {e}")
            raise
