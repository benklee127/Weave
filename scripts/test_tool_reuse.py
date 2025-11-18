#!/usr/bin/env python3
"""
Phase 2 Test Script: Tool Reuse Demonstration

This script demonstrates the semantic search and tool reuse capabilities
by generating a tool twice and showing the speedup on the second request.
"""

import sys
import os
from pathlib import Path
import time

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
    print("Autopoietic Agent: Phase 2 - Tool Reuse Demonstration")
    print("=" * 70)

    # Step 1: Health check
    print("\n[1/4] Running health check...")
    status = health_check()
    print(f"✓ Status: {status['status']}")
    print(f"✓ Version: {status['version']}")
    print(f"✓ Phase: {status['phase']}")
    print(f"✓ Librarian: {status['components']['librarian']}")
    print(f"✓ Library: {status['library']['total_tools']} tools indexed")

    # Test description
    test_description = "Create a function that calculates the sum of two numbers"

    # Step 2: First generation (cold path)
    print("\n[2/4] First Request: Generating tool (COLD PATH)...")
    print(f"Description: '{test_description}'")
    print("\nThis will:")
    print("  1. Search the library (no match expected)")
    print("  2. Generate tests")
    print("  3. Generate implementation")
    print("  4. Run tests and refine")
    print("  5. Register tool in library")

    start_time = time.time()
    result1 = generate_tool(description=test_description, max_tasks=3)
    cold_path_time = time.time() - start_time

    if result1["status"] == "error":
        print(f"✗ FAILED: {result1.get('error')}")
        return 1

    print(f"\n✓ First request completed in {cold_path_time:.2f}s")
    print(f"  Status: {result1['status']}")
    print(f"  Tasks verified: {result1['verified']}")
    print(f"  Tasks reused: {result1['reused']}")
    print(f"  Library now has: {result1['library_stats']['total_tools']} tools")

    # Step 3: Second generation (hot path)
    print("\n[3/4] Second Request: Same tool (HOT PATH - REUSE)...")
    print(f"Description: '{test_description}'")
    print("\nThis should:")
    print("  1. Search the library (MATCH EXPECTED)")
    print("  2. Reuse existing tool (skip generation)")
    print("  3. Return immediately")

    start_time = time.time()
    result2 = generate_tool(description=test_description, max_tasks=3)
    hot_path_time = time.time() - start_time

    if result2["status"] == "error":
        print(f"✗ FAILED: {result2.get('error')}")
        return 1

    print(f"\n✓ Second request completed in {hot_path_time:.2f}s")
    print(f"  Status: {result2['status']}")
    print(f"  Tasks verified: {result2['verified']}")
    print(f"  Tasks reused: {result2['reused']}")

    # Step 4: Compare performance
    print("\n[4/4] Performance Comparison:")
    print("=" * 70)
    print(f"Cold path (first request):  {cold_path_time:.2f}s")
    print(f"Hot path (second request):  {hot_path_time:.2f}s")

    if result2['reused'] > 0:
        speedup = cold_path_time / hot_path_time if hot_path_time > 0 else 0
        print(f"\n✓ SPEEDUP: {speedup:.1f}x faster!")
        print(f"  Time saved: {cold_path_time - hot_path_time:.2f}s")

        # Token savings
        tokens1 = result1["token_usage"]["total_tokens"]
        tokens2 = result2["token_usage"]["total_tokens"]
        token_savings = tokens1 - tokens2
        token_savings_pct = (token_savings / tokens1 * 100) if tokens1 > 0 else 0

        print(f"\n✓ TOKEN SAVINGS: {token_savings:,} tokens ({token_savings_pct:.1f}%)")
        print(f"  First request:  {tokens1:,} tokens")
        print(f"  Second request: {tokens2:,} tokens")

        # Cost savings (Claude Sonnet 4.5 pricing)
        cost1 = (result1["token_usage"]["input_tokens"] * 0.003 / 1000 +
                 result1["token_usage"]["output_tokens"] * 0.015 / 1000)
        cost2 = (result2["token_usage"]["input_tokens"] * 0.003 / 1000 +
                 result2["token_usage"]["output_tokens"] * 0.015 / 1000)
        cost_savings = cost1 - cost2

        print(f"\n✓ COST SAVINGS: ${cost_savings:.4f}")
        print(f"  First request:  ${cost1:.4f}")
        print(f"  Second request: ${cost2:.4f}")

    else:
        print("\n✗ WARNING: Tool was NOT reused on second request")
        print("  This indicates the semantic search may not be working correctly")
        print("  or the similarity threshold is too high")
        return 1

    # Detailed results
    print("\n" + "=" * 70)
    print("Detailed Results:")
    print("-" * 70)

    print("\nFirst Request:")
    for i, task_result in enumerate(result1["results"], 1):
        print(f"\n  Task {i}: {task_result['description']}")
        print(f"    Status: {task_result['status']}")
        if task_result["status"] == "verified":
            print(f"    Implementation: {task_result.get('implementation_path')}")

    print("\nSecond Request:")
    for i, task_result in enumerate(result2["results"], 1):
        print(f"\n  Task {i}: {task_result['description']}")
        print(f"    Status: {task_result['status']}")
        if task_result["status"] == "reused":
            print(f"    Reused tool: {task_result.get('reused_tool_name')}")
            print(f"    Similarity: {task_result.get('similarity', 0):.3f}")

    print("\n" + "=" * 70)
    print("✓ SUCCESS: Phase 2 tool reuse working correctly!")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
