# Autopoietic Agent: Quickstart Guide

## Prerequisites

- Python 3.11 or higher
- Docker Desktop (for sandboxing)
- Git
- 8GB+ RAM
- Anthropic API key

## 5-Minute Setup

### 1. Install uv (Fast Python Package Manager)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify installation
uv --version
```

### 2. Create Project

```bash
# Clone or create project directory
mkdir autopoietic-agent
cd autopoietic-agent

# Initialize with uv
uv init

# Create directory structure
mkdir -p src/agents src/core src/synthesis src/memory src/sandbox src/registry src/feedback src/utils
mkdir -p tools tests/unit tests/integration tests/generated
mkdir -p data/vector_db data/tool_metadata data/logs
```

### 3. Install Dependencies

```bash
# Core dependencies
uv add fastmcp anthropic pydantic pytest pytest-asyncio

# Semantic memory (Phase 2)
uv add chromadb sentence-transformers rank-bm25

# Hot-reload (Phase 3)
uv add watchdog

# Production (Phase 4)
uv add docker psycopg[binary] pgvector apscheduler prometheus-client

# Development tools
uv add --dev ruff mypy black ipython
```

### 4. Configure Environment

```bash
# Create .env file
cat > .env << EOF
# LLM Configuration
ANTHROPIC_API_KEY=your_api_key_here
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929

# Vector Database
VECTOR_DB_PATH=./data/vector_db
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Sandbox Configuration
SANDBOX_TYPE=docker
SANDBOX_TIMEOUT=300
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_QUOTA=50000

# Tool Registry
TOOL_DIRECTORY=./tools
AUTO_APPROVE_TOOLS=false

# Logging
LOG_LEVEL=INFO
LOG_FILE=./data/logs/orchestrator.log
EOF

# Add .env to .gitignore
echo ".env" >> .gitignore
echo "data/" >> .gitignore
echo "tools/*.py" >> .gitignore
echo "!tools/__init__.py" >> .gitignore
```

### 5. Create Minimal Orchestrator

```bash
cat > src/orchestrator.py << 'EOF'
"""
Autopoietic Agent: Meta-MCP Orchestrator
Minimal Phase 1 Implementation
"""
from fastmcp import FastMCP
from typing import Annotated
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize MCP server
mcp = FastMCP(
    name="autopoietic-agent",
    version="0.1.0",
    description="A self-propagating MCP layer with TDD and semantic search"
)

@mcp.tool()
def health_check() -> dict:
    """System health check - verifies the orchestrator is running"""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "phase": "1-foundation",
        "components": {
            "planner": "not_implemented",
            "auditor": "not_implemented",
            "librarian": "not_implemented",
            "architect": "not_implemented",
            "warden": "not_implemented"
        }
    }

@mcp.tool()
def generate_tool(description: str) -> dict:
    """
    Generate a new MCP tool based on a natural language description.

    This is the main entry point for the autopoietic system.
    Currently a stub for Phase 1 development.

    Args:
        description: Natural language description of the desired tool

    Returns:
        Status of tool generation process
    """
    return {
        "status": "not_implemented",
        "message": "Tool generation pipeline is under development",
        "description": description,
        "next_steps": [
            "Implement Planner for task decomposition",
            "Implement Auditor for test generation",
            "Implement Architect for code synthesis"
        ]
    }

if __name__ == "__main__":
    mcp.run()
EOF
```

### 6. Test Installation

```bash
# Activate virtual environment
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate  # Windows

# Test with MCP Inspector
uv run mcp dev src/orchestrator.py
```

You should see:
```
MCP Inspector running at http://localhost:6274
```

Open the URL and test the `health_check` tool.

---

## Phase 1: Build the Foundation

### Step 1: Implement Core Data Structures

Create `src/core/dag.py`:

```python
from pydantic import BaseModel
from typing import List, Optional, Dict
from enum import Enum
from datetime import datetime

class TaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"

class TaskNode(BaseModel):
    id: str
    description: str
    state: TaskState = TaskState.PENDING
    dependencies: List[str] = []
    input_schema: Dict = {}
    output_schema: Dict = {}
    test_suite_path: Optional[str] = None
    implementation_path: Optional[str] = None
    error_trace: Optional[str] = None
    created_at: str = datetime.now().isoformat()
    updated_at: str = datetime.now().isoformat()

class TaskGraph(BaseModel):
    nodes: List[TaskNode] = []

    def add_node(self, node: TaskNode):
        self.nodes.append(node)

    def get_node(self, task_id: str) -> Optional[TaskNode]:
        return next((n for n in self.nodes if n.id == task_id), None)

    def get_ready_tasks(self) -> List[TaskNode]:
        """Get tasks with all dependencies satisfied"""
        ready = []
        for node in self.nodes:
            if node.state != TaskState.PENDING:
                continue
            deps_satisfied = all(
                self.get_node(dep_id).state == TaskState.VERIFIED
                for dep_id in node.dependencies
            )
            if deps_satisfied:
                ready.append(node)
        return ready
```

### Step 2: Create LLM Client

Create `src/utils/llm_client.py`:

```python
import os
from anthropic import Anthropic
from typing import Optional

class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        self.client = Anthropic(api_key=self.api_key)

    def generate(self, prompt: str, system: Optional[str] = None,
                 max_tokens: int = 4096) -> str:
        """Generate completion from LLM"""
        messages = [{"role": "user", "content": prompt}]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a helpful AI assistant.",
            messages=messages
        )

        return response.content[0].text

    async def generate_async(self, prompt: str, system: Optional[str] = None,
                            max_tokens: int = 4096) -> str:
        """Async version of generate"""
        # For now, use sync version
        # TODO: Use AsyncAnthropic for true async
        return self.generate(prompt, system, max_tokens)
```

### Step 3: Implement Basic Planner

Create `src/agents/planner.py`:

```python
from src.core.dag import TaskGraph, TaskNode, TaskState
from src.utils.llm_client import LLMClient
import json
import uuid

class PlannerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def decompose(self, user_request: str) -> TaskGraph:
        """Decompose user request into a task graph"""

        prompt = f"""You are a task decomposition specialist. Break this request into atomic subtasks.

User Request: {user_request}

Respond with a JSON array of tasks. Each task must:
1. Be implementable as a single Python function
2. Have clear input/output schemas
3. List dependencies by index (0-based)

Format:
[
  {{
    "description": "Task description",
    "dependencies": [],
    "input_schema": {{"type": "object", "properties": {{}}}},
    "output_schema": {{"type": "object", "properties": {{}}}}
  }}
]

Rules:
- Maximum 5 tasks
- Keep tasks atomic
- Order by dependencies (dependencies first)
"""

        system = "You are an expert at breaking down complex tasks into simple, testable functions."

        response = self.llm.generate(prompt, system=system)

        # Parse JSON response
        tasks_json = self._extract_json(response)

        # Build task graph
        graph = TaskGraph()
        for i, task_data in enumerate(tasks_json):
            node = TaskNode(
                id=f"task_{uuid.uuid4().hex[:8]}",
                description=task_data["description"],
                dependencies=[
                    f"task_{dep}" for dep in task_data.get("dependencies", [])
                ],
                input_schema=task_data.get("input_schema", {}),
                output_schema=task_data.get("output_schema", {})
            )
            graph.add_node(node)

        return graph

    def _extract_json(self, text: str) -> list:
        """Extract JSON from LLM response"""
        # Find JSON array in response
        start = text.find('[')
        end = text.rfind(']') + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array found in response")

        json_str = text[start:end]
        return json.loads(json_str)
```

### Step 4: Test the Planner

```bash
# Create test file
cat > tests/unit/test_planner.py << 'EOF'
import pytest
from src.agents.planner import PlannerAgent
from src.utils.llm_client import LLMClient

def test_planner_decompose():
    llm = LLMClient()
    planner = PlannerAgent(llm)

    request = "Create a function to calculate the Fibonacci sequence up to n terms"
    graph = planner.decompose(request)

    assert len(graph.nodes) > 0
    assert all(node.description for node in graph.nodes)
    print(f"Decomposed into {len(graph.nodes)} tasks:")
    for node in graph.nodes:
        print(f"  - {node.description}")

if __name__ == "__main__":
    test_planner_decompose()
EOF

# Run test
uv run python tests/unit/test_planner.py
```

---

## Next Steps

### Complete Phase 1
- [ ] Implement Auditor (test generation)
- [ ] Implement Architect (code synthesis)
- [ ] Implement basic subprocess test runner
- [ ] Wire everything together in orchestrator
- [ ] Test end-to-end: request → decompose → test → code → verify

### Move to Phase 2
- [ ] Set up ChromaDB
- [ ] Implement embedding generation
- [ ] Implement Librarian agent
- [ ] Test tool retrieval

### Reference Documentation
- [Full Implementation Plan](./AUTOPOIETIC_AGENT_PLAN.md)
- [System Architecture](./ARCHITECTURE.md)
- [FastMCP Documentation](https://gofastmcp.com)
- [MCP Specification](https://modelcontextprotocol.io)

---

## Troubleshooting

### "Module not found" errors
```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
uv sync
```

### Docker connection errors
```bash
# Verify Docker is running
docker ps

# Test Docker access
docker run hello-world
```

### Anthropic API errors
```bash
# Verify API key is set
echo $ANTHROPIC_API_KEY

# Test API access
uv run python -c "from anthropic import Anthropic; print(Anthropic().messages.create(model='claude-sonnet-4-5-20250929', max_tokens=10, messages=[{'role':'user','content':'hi'}]))"
```

### MCP Inspector not starting
```bash
# Install mcp CLI
uv add mcp

# Try running with verbose output
uv run mcp dev src/orchestrator.py --verbose
```

---

## Development Workflow

```bash
# 1. Make changes to code
vim src/agents/planner.py

# 2. Run tests
uv run pytest tests/unit -v

# 3. Test with MCP Inspector
uv run mcp dev src/orchestrator.py

# 4. Iterate
```

---

## Project Status Checklist

### Phase 1: Foundation
- [ ] Project structure created
- [ ] Core data models (TaskNode, TaskGraph)
- [ ] LLM client integration
- [ ] Planner agent (task decomposition)
- [ ] Auditor agent (test generation)
- [ ] Architect agent (code synthesis)
- [ ] Basic subprocess runner
- [ ] End-to-end test: Fibonacci function

### Phase 2: Semantic Memory
- [ ] ChromaDB integration
- [ ] Embedding generation
- [ ] Librarian agent
- [ ] Hybrid search (vector + BM25)
- [ ] Tool registration hook
- [ ] Test: Tool reuse

### Phase 3: Hot-Reload
- [ ] Watchdog file monitoring
- [ ] Dynamic module loading
- [ ] listChanged notifications
- [ ] Test: Live tool updates

### Phase 4: Production
- [ ] Docker sandbox
- [ ] MCP Sampling approval
- [ ] Janitor regression testing
- [ ] Self-healing workflow
- [ ] Migration to pgvector
- [ ] Observability (Prometheus)

---

**Ready to Start?**

```bash
# Start development
cd autopoietic-agent
source .venv/bin/activate
uv run mcp dev src/orchestrator.py
```

Happy building! 🚀
