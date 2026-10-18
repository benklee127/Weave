"""
Auditor Agent: Test-Driven Development Engine

This agent generates comprehensive pytest test suites from task specifications
and validates test quality using a "Critic" pattern.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Union

from src.core.schemas import (
    TaskNode,
    TestSuiteMetadata,
    TestValidationResult,
)
from src.utils.json_extract import JSONExtractionError, extract_json
from src.utils.llm_client import LLMClientProtocol
from src.utils.naming import derive_function_name, unique_path
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

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        test_output_dir: Union[str, Path] = "./tests/generated",
    ):
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

    def generate_test_suite(
        self, task: TaskNode, dependency_context: Optional[str] = None
    ) -> TestSuiteMetadata:
        """
        Generate a pytest test suite for a task.

        Args:
            task: TaskNode to generate tests for
            dependency_context: Optional signatures of verified upstream tools

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
        if dependency_context:
            prompt += (
                "\n\nAVAILABLE UPSTREAM TOOLS (already verified):\n"
                f"{dependency_context}"
            )

        try:
            # Call LLM
            test_code = self.llm.generate(
                prompt=prompt,
                system=AUDITOR_SYSTEM,
                max_tokens=4096,
                temperature=0.5,  # Lower temperature for more consistent tests
                agent=self.agent_id,
            )

            # Clean up the code (remove markdown if present)
            test_code = self._clean_code(test_code)

            # Save to file
            self.test_output_dir.mkdir(parents=True, exist_ok=True)
            test_file_path = unique_path(
                self.test_output_dir, "test_", function_name, task.id, test_code
            )
            test_file_path.write_text(test_code, encoding="utf-8")

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
                agent=self.agent_id,
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
                recommendation=str(result_data.get("recommendation", "REJECT")).strip().upper(),
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

        Examples:
            "Calculate compound interest" -> "calculate_compound_interest"
        """
        return derive_function_name(description)

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
        Extract the JSON object from an LLM response (raw_decode based).

        Raises:
            ValueError: If no valid JSON object found
        """
        try:
            return extract_json(text, expect=dict)
        except JSONExtractionError as e:
            raise ValueError(str(e))
