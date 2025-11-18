"""
LLM prompt templates for all agents.

This module contains all prompts used throughout the system.
Prompts are versioned and should be tested for effectiveness.
"""

from typing import Dict, Any
import json


# ============================================================================
# PLANNER AGENT PROMPTS
# ============================================================================

PLANNER_SYSTEM = """You are a task decomposition specialist. You excel at breaking complex problems into simple, testable components. You always respond with valid JSON and never include explanatory text outside the JSON structure."""

PLANNER_DECOMPOSITION_PROMPT = """You are an expert software architect specializing in task decomposition.

USER REQUEST:
{user_request}

Your task is to break this request into atomic subtasks that can each be implemented as a single Python function.

REQUIREMENTS:
1. Each task must be small enough to implement in < 50 lines of code
2. Tasks must have explicit input/output schemas (JSON Schema format)
3. Define dependencies clearly using task indices
4. Order tasks topologically (dependencies before dependents)
5. Maximum {max_tasks} tasks total
6. Each task must be independently testable

OUTPUT FORMAT (JSON):
[
  {{
    "description": "Clear, imperative description of what this function does",
    "dependencies": [0, 1],
    "input_schema": {{
      "type": "object",
      "properties": {{
        "param_name": {{"type": "string", "description": "What this parameter is"}}
      }},
      "required": ["param_name"]
    }},
    "output_schema": {{
      "type": "object",
      "properties": {{
        "result": {{"type": "string", "description": "What is returned"}}
      }}
    }}
  }}
]

EXAMPLE:
Request: "Analyze sentiment of my last 10 tweets"
Response:
[
  {{
    "description": "Fetch last 10 tweets for a given username",
    "dependencies": [],
    "input_schema": {{
      "type": "object",
      "properties": {{
        "username": {{"type": "string", "description": "Twitter username"}}
      }},
      "required": ["username"]
    }},
    "output_schema": {{
      "type": "object",
      "properties": {{
        "tweets": {{"type": "array", "items": {{"type": "string"}}}}
      }}
    }}
  }},
  {{
    "description": "Analyze sentiment of a list of text strings",
    "dependencies": [0],
    "input_schema": {{
      "type": "object",
      "properties": {{
        "texts": {{"type": "array", "items": {{"type": "string"}}}}
      }},
      "required": ["texts"]
    }},
    "output_schema": {{
      "type": "object",
      "properties": {{
        "sentiments": {{
          "type": "array",
          "items": {{
            "type": "object",
            "properties": {{
              "text": {{"type": "string"}},
              "sentiment": {{"type": "string", "enum": ["positive", "negative", "neutral"]}},
              "confidence": {{"type": "number"}}
            }}
          }}
        }}
      }}
    }}
  }}
]

Now decompose the user request above. Return ONLY the JSON array, nothing else."""


# ============================================================================
# AUDITOR AGENT PROMPTS
# ============================================================================

AUDITOR_SYSTEM = """You are an expert in Test-Driven Development. You write rigorous, comprehensive test suites that catch edge cases and validate correct behavior. Your tests are the specification."""

AUDITOR_TEST_GENERATION_PROMPT = """You are a Test-Driven Development expert. Generate a complete pytest test suite for this function specification.

FUNCTION SPECIFICATION:
Description: {task_description}

Input Schema:
{input_schema}

Output Schema:
{output_schema}

REQUIREMENTS:
1. Generate at least 5 test functions covering:
   - Happy path with valid inputs
   - Edge cases (empty inputs, None values, boundary conditions)
   - Invalid inputs (wrong types, out of range)
   - Error handling
2. Use mocks for external dependencies (APIs, databases, file I/O)
3. Each test must have at least 2 assertions
4. Tests must be deterministic (no random values, fixed timestamps)
5. The test suite must FAIL when run against an empty function implementation
6. Use pytest fixtures for setup/teardown
7. Include clear docstrings for each test
8. The function to test will be named: {function_name}

OUTPUT FORMAT:
Generate ONLY the Python code for the test file. No explanations, no markdown, just pure Python code.

Begin with imports, then fixtures, then test functions.

Example structure:

import pytest
from unittest.mock import Mock, patch
from datetime import datetime

# Import the function (adjust path as needed)
# from tools.{function_name} import {function_name}

@pytest.fixture
def mock_data():
    \"\"\"Fixture providing mock data\"\"\"
    return {{"key": "value"}}

def test_{function_name}_success(mock_data):
    \"\"\"Test successful execution\"\"\"
    # TODO: Implementation
    pass

def test_{function_name}_invalid_input():
    \"\"\"Test handling of invalid input\"\"\"
    # TODO: Implementation
    pass

def test_{function_name}_edge_case():
    \"\"\"Test edge case handling\"\"\"
    # TODO: Implementation
    pass

Now generate the complete test suite. Return ONLY Python code."""


# ============================================================================
# AUDITOR CRITIC PROMPTS
# ============================================================================

AUDITOR_CRITIC_PROMPT = """You are a test quality auditor. Analyze this test suite and determine if it meets quality standards.

TEST SUITE:
```python
{test_code}
```

QUALITY CRITERIA:
1. **Assertion Density**: At least 20% of lines should be assertions
2. **Coverage**: Tests cover happy path, edge cases, and error conditions
3. **No Vacuous Assertions**: No tests with only `assert True` or trivial checks
4. **Mocking**: External dependencies are properly mocked
5. **Determinism**: No random values or time dependencies without mocking
6. **Will Fail on Empty**: Tests will fail if run against an empty function

ANALYSIS TASKS:
1. Count total lines of code (excluding blank lines and comments)
2. Count assertion statements (assert, pytest.raises, etc.)
3. Identify any vacuous assertions
4. Check if external dependencies are mocked
5. Verify test will fail against empty implementation

OUTPUT FORMAT (JSON):
{{
  "valid": true/false,
  "assertion_count": 12,
  "total_lines": 45,
  "assertion_density": 0.27,
  "issues": [
    "Test 'test_foo' only has assert True",
    "API call not mocked in test_bar"
  ],
  "recommendation": "APPROVE",
  "reason": "Explanation of recommendation"
}}

Valid recommendations: "APPROVE", "REJECT", "NEEDS_REVISION"

Now analyze the test suite. Return ONLY the JSON object, nothing else."""


# ============================================================================
# ARCHITECT AGENT PROMPTS
# ============================================================================

ARCHITECT_SYSTEM = """You are an expert Python developer who writes clean, tested, production-quality code. You always include type hints, comprehensive docstrings, and proper error handling."""

ARCHITECT_SYNTHESIS_PROMPT = """You are an expert Python developer. Generate implementation code that passes this test suite.

FUNCTION SPECIFICATION:
{task_description}

FUNCTION NAME: {function_name}

TEST SUITE:
```python
{test_code}
```

INPUT SCHEMA:
{input_schema}

OUTPUT SCHEMA:
{output_schema}

REQUIREMENTS:
1. Write clean, idiomatic Python 3.11+ code
2. Use type hints for all parameters and return values
3. Include comprehensive docstring (Google style)
4. Handle all edge cases tested in the test suite
5. Code must pass ALL tests in the test suite
6. Maximum 100 lines of code
7. No use of eval(), exec(), or __import__()
8. Log errors but don't print debug statements
9. Return ONLY the function implementation (no imports of FastMCP, no decorators)

OUTPUT FORMAT:
Return ONLY the Python function code. Start directly with the function definition.

Example structure:

def {function_name}(param: str) -> Dict[str, Any]:
    \"\"\"
    Brief description.

    This function does X by doing Y and Z.

    Args:
        param: Description of parameter

    Returns:
        Dictionary containing:
        - key1: Description
        - key2: Description

    Raises:
        ValueError: When X condition is not met
        TypeError: When Y is wrong type

    Examples:
        >>> {function_name}("test")
        {{'result': 'success'}}
    \"\"\"
    # Implementation here
    pass

Now generate the complete implementation. Return ONLY Python code, starting with `def`."""


# ============================================================================
# ARCHITECT REFINER PROMPTS
# ============================================================================

ARCHITECT_REFINER_PROMPT = """You are debugging code that failed its test suite. Fix the implementation to pass all tests.

ORIGINAL IMPLEMENTATION:
```python
{original_code}
```

TEST SUITE:
```python
{test_code}
```

TEST FAILURE OUTPUT:
{error_trace}

REQUIREMENTS:
1. Analyze the error trace to understand what failed
2. Fix ONLY the bug causing the failure
3. Do not rewrite the entire function unless necessary
4. Preserve the function signature and docstring
5. Ensure type hints remain correct
6. Return the COMPLETE fixed implementation

OUTPUT FORMAT:
Return ONLY the fixed Python function code. Start with `def`."""


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def format_prompt(template: str, **kwargs: Any) -> str:
    """
    Format a prompt template with variables.

    Args:
        template: Prompt template string
        **kwargs: Variables to substitute

    Returns:
        Formatted prompt string
    """
    # Convert dicts to pretty JSON for better readability
    formatted_kwargs = {}
    for key, value in kwargs.items():
        if isinstance(value, dict):
            formatted_kwargs[key] = json.dumps(value, indent=2)
        else:
            formatted_kwargs[key] = value

    return template.format(**formatted_kwargs)
