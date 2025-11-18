"""
Auditor Agent: Test-Driven Development Engine

This agent generates comprehensive pytest test suites from task specifications
and validates test quality using a "Critic" pattern.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from src.core.schemas import (
    TaskNode,
    TestSuiteMetadata,
    TestValidationResult,
)
from src.utils.llm_client import LLMClient
from src.utils.prompts import (
    AUDITOR_SYSTEM,
    AUDITOR_TEST_GENERATION_PROMPT,
    AUDITOR_CRITIC_PROMPT,
    format_prompt,
)

logger = logging.getLogger(__name__)


class AuditorAgent:
    """
    Agent responsible for generating and validating test suites.

    The Auditor implements Test-Driven Development by generating tests
    BEFORE implementation code, creating a rigid specification for the
    Architect to satisfy.
    """

    def __init__(self, llm_client: LLMClient, test_output_dir: str = "./tests/generated"):
        """
        Initialize the Auditor Agent.

        Args:
            llm_client: LLM client for making API calls
            test_output_dir: Directory to save generated tests
        """
        self.llm = llm_client
        self.agent_id = "auditor"
        self.test_output_dir = Path(test_output_dir)
        self.test_output_dir.mkdir(parents=True, exist_ok=True)

    def generate_test_suite(self, task: TaskNode) -> TestSuiteMetadata:
        """
        Generate a pytest test suite for a task.

        Args:
            task: TaskNode to generate tests for

        Returns:
            TestSuiteMetadata with info about generated tests

        Raises:
            ValueError: If test generation fails
        """
        logger.info(f"Generating test suite for task: {task.id}")

        # Derive function name from task description
        function_name = self._derive_function_name(task.description)

        # Format the prompt
        prompt = format_prompt(
            AUDITOR_TEST_GENERATION_PROMPT,
            task_description=task.description,
            input_schema=task.input_schema,
            output_schema=task.output_schema,
            function_name=function_name,
        )

        try:
            # Call LLM
            test_code = self.llm.generate(
                prompt=prompt,
                system=AUDITOR_SYSTEM,
                max_tokens=4096,
                temperature=0.5,  # Lower temperature for more consistent tests
            )

            # Clean up the code (remove markdown if present)
            test_code = self._clean_code(test_code)

            # Save to file
            test_file_path = self.test_output_dir / f"test_{function_name}.py"
            test_file_path.write_text(test_code)

            # Extract metadata
            metadata = self._extract_test_metadata(test_code, task.id, str(test_file_path))

            logger.info(
                f"Generated test suite: {metadata.assertion_count} assertions "
                f"in {len(metadata.test_functions)} test functions"
            )

            # Update task with test path
            task.test_suite_path = str(test_file_path)

            return metadata

        except Exception as e:
            logger.error(f"Test generation failed: {e}")
            raise ValueError(f"Failed to generate test suite: {e}")

    def validate_test_quality(self, test_code: str) -> TestValidationResult:
        """
        Validate test quality using the Critic pattern.

        Args:
            test_code: Python test code to validate

        Returns:
            TestValidationResult with quality assessment

        Raises:
            ValueError: If validation fails
        """
        logger.info("Validating test quality with Critic")

        # Format the prompt
        prompt = format_prompt(
            AUDITOR_CRITIC_PROMPT,
            test_code=test_code,
        )

        try:
            # Call LLM
            response_text = self.llm.generate(
                prompt=prompt,
                system=AUDITOR_SYSTEM,
                max_tokens=2048,
                temperature=0.3,  # Low temperature for consistent analysis
            )

            # Parse JSON response
            result_data = self._extract_and_parse_json(response_text)

            # Create validation result
            result = TestValidationResult(
                valid=result_data.get("valid", False),
                assertion_count=result_data.get("assertion_count", 0),
                total_lines=result_data.get("total_lines", 0),
                assertion_density=result_data.get("assertion_density", 0.0),
                issues=result_data.get("issues", []),
                recommendation=result_data.get("recommendation", "REJECT"),
                reason=result_data.get("reason", "No reason provided"),
            )

            logger.info(
                f"Test validation: {result.recommendation} "
                f"(density: {result.assertion_density:.2f}, "
                f"{len(result.issues)} issues)"
            )

            return result

        except Exception as e:
            logger.error(f"Test validation failed: {e}")
            raise ValueError(f"Failed to validate test quality: {e}")

    def _derive_function_name(self, description: str) -> str:
        """
        Derive a snake_case function name from a task description.

        Args:
            description: Task description

        Returns:
            Valid Python function name

        Examples:
            "Calculate compound interest" -> "calculate_compound_interest"
            "Fetch user data from API" -> "fetch_user_data_from_api"
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
        Clean LLM-generated code (remove markdown, extra whitespace).

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

        return code

    def _extract_test_metadata(
        self, test_code: str, task_id: str, test_file_path: str
    ) -> TestSuiteMetadata:
        """
        Extract metadata from test code.

        Args:
            test_code: Python test code
            task_id: Associated task ID
            test_file_path: Path to test file

        Returns:
            TestSuiteMetadata
        """
        # Count test functions
        test_functions = re.findall(r'def (test_\w+)\(', test_code)

        # Count assertions (rough estimate)
        assertion_count = (
            test_code.count('assert ')
            + test_code.count('pytest.raises')
            + test_code.count('assert_')
        )

        # Count total lines (excluding blank lines)
        lines = [line for line in test_code.split('\n') if line.strip()]
        total_lines = len(lines)

        # Calculate assertion density
        assertion_density = assertion_count / total_lines if total_lines > 0 else 0.0

        return TestSuiteMetadata(
            task_id=task_id,
            test_file_path=test_file_path,
            test_functions=test_functions,
            assertion_count=assertion_count,
            assertion_density=assertion_density,
            coverage_estimate=0.0,  # TODO: Could estimate from test cases
            validation_status="pending",
        )

    def _extract_and_parse_json(self, text: str) -> dict:
        """
        Extract and parse JSON object from LLM response.

        Args:
            text: Raw text from LLM

        Returns:
            Parsed JSON dict

        Raises:
            ValueError: If no valid JSON found
        """
        # Try to find JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)

        if match:
            json_str = match.group(0)
        else:
            json_str = text.strip()

        try:
            data = json.loads(json_str)

            if not isinstance(data, dict):
                raise ValueError("Expected JSON object")

            return data

        except json.JSONDecodeError:
            # Try to find between code blocks
            code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
            if code_block_match:
                return json.loads(code_block_match.group(1))

            raise ValueError("Could not extract valid JSON from response")
