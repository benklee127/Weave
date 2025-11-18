#!/usr/bin/env python3
"""
Standalone test script for the Fibonacci generation example.

This script can be run directly without pytest to test the entire pipeline.
"""

import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

# Check API key
if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY environment variable not set")
    print("Please set it in your .env file or export it")
    sys.exit(1)

from src.orchestrator import generate_tool, health_check

def main():
    print("=" * 70)
    print("Autopoietic Agent: Fibonacci Generation Test")
    print("=" * 70)

    # Step 1: Health check
    print("\n[1/3] Running health check...")
    status = health_check()
    print(f"✓ Status: {status['status']}")
    print(f"✓ Version: {status['version']}")
    print(f"✓ Components operational: {sum(1 for c in status['components'].values() if c == 'operational')}")

    # Step 2: Generate Fibonacci tool
    print("\n[2/3] Generating Fibonacci function...")
    print("Description: 'Create a function that calculates the Fibonacci sequence up to n terms'")

    result = generate_tool(
        description="Create a function that calculates the Fibonacci sequence up to n terms",
        max_tasks=5
    )

    # Step 3: Report results
    print("\n[3/3] Results:")
    print("=" * 70)

    if result["status"] == "error":
        print(f"✗ FAILED: {result.get('error')}")
        return 1

    print(f"Status: {result['status']}")
    print(f"Total tasks: {result['total_tasks']}")
    print(f"Verified: {result['verified']}")
    print(f"Failed: {result['failed']}")

    print("\nDetailed Results:")
    print("-" * 70)

    for i, task_result in enumerate(result["results"], 1):
        status_symbol = "✓" if task_result["status"] == "verified" else "✗"
        print(f"\n{status_symbol} Task {i}: {task_result['description']}")
        print(f"  Status: {task_result['status']}")

        if task_result["status"] == "verified":
            print(f"  Implementation: {task_result['implementation_path']}")
            print(f"  Tests: {task_result['test_path']}")
            print(f"  Tests passed: {task_result['tests_passed']}")
            print(f"  Execution time: {task_result['execution_time']:.2f}s")
        else:
            print(f"  Error: {task_result.get('error', 'Unknown error')}")

    print("\nToken Usage:")
    print("-" * 70)
    token_usage = result["token_usage"]
    print(f"Input tokens:  {token_usage['input_tokens']:,}")
    print(f"Output tokens: {token_usage['output_tokens']:,}")
    print(f"Total tokens:  {token_usage['total_tokens']:,}")

    # Estimate cost (Claude Sonnet 4.5 pricing as of Nov 2025)
    input_cost = token_usage['input_tokens'] * 0.003 / 1000  # $3 per million input tokens
    output_cost = token_usage['output_tokens'] * 0.015 / 1000  # $15 per million output tokens
    total_cost = input_cost + output_cost

    print(f"Estimated cost: ${total_cost:.4f}")

    print("\n" + "=" * 70)

    if result["verified"] > 0:
        print("✓ SUCCESS: Tool generation completed successfully!")
        return 0
    else:
        print("✗ FAILURE: No tasks were verified")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
