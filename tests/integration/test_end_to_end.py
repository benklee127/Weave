"""
End-to-end integration test for the Autopoietic Agent.

This test verifies the entire pipeline: decomposition → test generation →
code synthesis → verification.
"""

import pytest
import os
from pathlib import Path

# Ensure we have an API key
pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set"
)


def test_fibonacci_generation():
    """
    Test generating a Fibonacci function from scratch.

    This is the canonical example for Phase 1.
    """
    from src.orchestrator import generate_tool, llm_client

    # Reset token usage for this test
    llm_client.reset_token_usage()

    # Generate tool
    result = generate_tool(
        description="Create a function that calculates the Fibonacci sequence up to n terms",
        max_tasks=5
    )

    # Verify result structure
    assert result["status"] in ["completed", "error"], f"Unexpected status: {result}"

    if result["status"] == "error":
        pytest.fail(f"Tool generation failed: {result.get('error')}")

    # Verify at least one task was created
    assert result["total_tasks"] > 0, "No tasks were generated"

    # Verify at least one task succeeded
    assert result["verified"] > 0, "No tasks were verified"

    # Check that implementation files were created
    assert result["results"], "No results returned"

    for task_result in result["results"]:
        if task_result["status"] == "verified":
            impl_path = task_result.get("implementation_path")
            assert impl_path, "No implementation path in result"

            # Verify file exists
            assert Path(impl_path).exists(), f"Implementation file not found: {impl_path}"

            # Verify test path
            test_path = task_result.get("test_path")
            assert test_path, "No test path in result"
            assert Path(test_path).exists(), f"Test file not found: {test_path}"

            print(f"\n✓ Task verified: {task_result['description']}")
            print(f"  Implementation: {impl_path}")
            print(f"  Tests: {test_path}")
            print(f"  Tests passed: {task_result['tests_passed']}")
            print(f"  Execution time: {task_result['execution_time']:.2f}s")

    # Print token usage
    token_usage = result["token_usage"]
    print(f"\nToken usage:")
    print(f"  Input: {token_usage['input_tokens']}")
    print(f"  Output: {token_usage['output_tokens']}")
    print(f"  Total: {token_usage['total_tokens']}")


def test_simple_math_function():
    """
    Test generating a simple mathematical function.
    """
    from src.orchestrator import generate_tool

    result = generate_tool(
        description="Create a function that calculates the area of a circle given its radius",
        max_tasks=3
    )

    assert result["status"] == "completed", f"Generation failed: {result.get('error')}"
    assert result["verified"] > 0, "No tasks were verified"

    print(f"\n✓ Generated tool for circle area calculation")
    print(f"  Tasks verified: {result['verified']}/{result['total_tasks']}")


def test_health_check():
    """Test the health check endpoint."""
    from src.orchestrator import health_check

    status = health_check()

    assert status["status"] == "healthy"
    assert status["version"] == "0.1.0"
    assert "components" in status
    assert status["components"]["planner"] == "operational"
    assert status["components"]["auditor"] == "operational"
    assert status["components"]["architect"] == "operational"
    assert status["components"]["warden"] == "operational"

    print("\n✓ Health check passed")
    print(f"  Status: {status['status']}")
    print(f"  Components: {len([c for c in status['components'].values() if c == 'operational'])} operational")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
