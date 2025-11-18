# Autopoietic Agent: Implementation Plan

## Executive Summary

This document outlines the implementation plan for a self-propagating Model Context Protocol (MCP) layer that can autonomously generate, test, and register new tools at runtime. The system uses Test-Driven Development (TDD), semantic search, and sandboxed execution to create a "liquid software" environment.

## Project Structure

```
autopoietic-agent/
├── pyproject.toml                 # Project dependencies and metadata
├── README.md                      # Project documentation
├── .env.example                   # Environment configuration template
│
├── src/
│   ├── __init__.py
│   ├── orchestrator.py            # Meta-MCP Orchestrator (main entry point)
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py                # Base agent class
│   │   ├── planner.py             # The Planner (Decomposer)
│   │   ├── auditor.py             # The Auditor (TDD Engine)
│   │   ├── librarian.py           # The Librarian (Semantic Search)
│   │   ├── architect.py           # The Architect (Code Synthesis)
│   │   └── warden.py              # The Warden (Runtime & Security)
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── dag.py                 # DAG representation for task dependencies
│   │   ├── task_state.py          # Task state management
│   │   ├── message_bus.py         # Inter-agent communication
│   │   └── schemas.py             # Pydantic models for data structures
│   │
│   ├── synthesis/
│   │   ├── __init__.py
│   │   ├── code_generator.py      # LLM-based code generation
│   │   ├── test_generator.py      # Test suite generation
│   │   ├── test_validator.py      # Test quality validation ("Critic")
│   │   └── templates/             # Code templates
│   │       ├── tool_template.py
│   │       └── test_template.py
│   │
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── vector_store.py        # Vector database integration
│   │   ├── embeddings.py          # Embedding generation
│   │   ├── hybrid_search.py       # Hybrid retrieval (vector + BM25)
│   │   └── tool_index.py          # Tool indexing and metadata
│   │
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── docker_runner.py       # Docker sandbox execution
│   │   ├── firecracker_runner.py  # Firecracker microVM (Phase 4)
│   │   ├── resource_limiter.py    # cgroups and resource quotas
│   │   └── network_policy.py      # Network isolation rules
│   │
│   ├── registry/
│   │   ├── __init__.py
│   │   ├── tool_registry.py       # Dynamic tool registration
│   │   ├── hot_reload.py          # Watchdog-based hot-reloading
│   │   └── mcp_notifier.py        # listChanged notifications
│   │
│   ├── feedback/
│   │   ├── __init__.py
│   │   ├── sampling.py            # MCP sampling for user approval
│   │   ├── janitor.py             # Regression testing and self-healing
│   │   └── user_feedback.py       # User feedback integration
│   │
│   └── utils/
│       ├── __init__.py
│       ├── llm_client.py          # LLM API client (Anthropic Claude)
│       ├── prompts.py             # Prompt templates
│       └── logging_config.py      # Structured logging
│
├── tools/                         # Dynamically generated tools (hot-reload)
│   ├── __init__.py
│   └── .gitkeep
│
├── tests/
│   ├── __init__.py
│   ├── unit/                      # Unit tests for core components
│   ├── integration/               # Integration tests
│   ├── fixtures/                  # Test fixtures and mocks
│   └── generated/                 # Auto-generated test suites
│
├── data/
│   ├── vector_db/                 # ChromaDB/pgvector persistence
│   ├── tool_metadata/             # Tool metadata and schemas
│   └── logs/                      # System logs
│
└── scripts/
    ├── setup_vector_db.py         # Initialize vector database
    ├── run_regression.py          # Manual regression testing
    └── benchmark.py               # Performance benchmarking
```

---

## Technology Stack

### Core Framework
- **Python 3.11+** - Language runtime
- **uv** - Fast Python package manager
- **FastMCP** - MCP server/client SDK
- **Pydantic v2** - Data validation and schema generation

### Agent Framework
- **LangChain/LangGraph** (optional) - Agent orchestration patterns
- **Anthropic Claude API** - LLM for reasoning and code generation

### Testing Infrastructure
- **pytest** - Test framework
- **pytest-asyncio** - Async test support
- **pytest-timeout** - Test timeout enforcement
- **mutation-testing (mutmut)** - Test quality validation

### Semantic Memory
- **ChromaDB** - Local vector database (Phase 2-3)
- **pgvector** - PostgreSQL vector extension (Phase 4 production)
- **sentence-transformers** - Embedding models
  - `all-MiniLM-L6-v2` - Fast, local embeddings
  - `voyage-code-2` - Code-optimized (optional API)
- **rank-bm25** - Sparse retrieval for keyword matching

### Sandboxing & Security
- **Docker SDK for Python** - Container orchestration
- **watchdog** - Filesystem event monitoring
- **Firecracker** (Phase 4) - MicroVM isolation
- **seccomp** - System call filtering

### Utilities
- **httpx** - Async HTTP client
- **rich** - Terminal UI and logging
- **loguru** - Structured logging

---

## Phase 1: Foundation (Static MCP with TDD)

**Goal:** Establish the Meta-MCP Orchestrator capable of task decomposition and test generation.

**Duration:** 1-2 weeks

### Tasks

#### 1.1 Project Setup
- [ ] Initialize project with `uv init`
- [ ] Configure `pyproject.toml` with dependencies
- [ ] Set up pre-commit hooks (ruff, mypy, black)
- [ ] Create directory structure
- [ ] Configure environment variables (.env)

#### 1.2 MCP Server Scaffold
- [ ] Create basic FastMCP server in `orchestrator.py`
- [ ] Implement health check tool
- [ ] Test connection with MCP Inspector
- [ ] Set up structured logging with loguru

```python
# src/orchestrator.py (initial scaffold)
from fastmcp import FastMCP
from typing import Annotated

mcp = FastMCP("autopoietic-agent")

@mcp.tool()
def health_check() -> dict:
    """System health check"""
    return {"status": "healthy", "version": "0.1.0"}

if __name__ == "__main__":
    mcp.run()
```

#### 1.3 Core Data Structures
- [ ] Implement `TaskNode` class in `core/dag.py`
- [ ] Implement `TaskState` enum (pending, in_progress, verified, failed)
- [ ] Create `TaskGraph` class with topological sort
- [ ] Build `MessageBus` for inter-agent communication

```python
# core/dag.py (skeleton)
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class TaskState(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"

@dataclass
class TaskNode:
    id: str
    description: str
    state: TaskState
    dependencies: List[str]
    input_schema: dict
    output_schema: dict
    test_suite: Optional[str] = None
    implementation: Optional[str] = None
```

#### 1.4 The Planner Agent (v1)
- [ ] Implement `PlannerAgent` in `agents/planner.py`
- [ ] Create prompt template for task decomposition
- [ ] Integrate with Claude API via `utils/llm_client.py`
- [ ] Parse LLM output into `TaskGraph`
- [ ] Implement ReAct pattern with retries

**Prompt Template:**
```
You are a task decomposition specialist. Given a user request, break it into atomic subtasks.

User Request: {user_request}

Respond with a JSON array of tasks in this format:
[
  {
    "id": "task_1",
    "description": "Fetch API logs from the last 7 days",
    "dependencies": [],
    "input_schema": {...},
    "output_schema": {...}
  },
  ...
]

Rules:
1. Each task must be small enough to implement as a single function
2. Clearly define dependencies (use task IDs)
3. Provide precise JSON schemas for inputs/outputs
4. Order tasks topologically
```

#### 1.5 The Auditor Agent (v1)
- [ ] Implement `AuditorAgent` in `agents/auditor.py`
- [ ] Create test generation prompt template
- [ ] Generate pytest test files from `TaskNode`
- [ ] Implement basic test validation (assertion density check)
- [ ] Save generated tests to `tests/generated/`

**Test Generation Logic:**
```python
# agents/auditor.py (excerpt)
def generate_test_suite(self, task: TaskNode) -> str:
    """Generate pytest test suite for a task"""
    prompt = f"""
Generate a complete pytest test suite for this function specification:

Description: {task.description}
Input Schema: {task.input_schema}
Output Schema: {task.output_schema}

Requirements:
1. Test the happy path with valid inputs
2. Test edge cases (empty inputs, None, invalid types)
3. Use mocks for external dependencies
4. Include at least 5 distinct assertions
5. Ensure the test will FAIL against an empty implementation
"""
    test_code = self.llm_client.generate(prompt)
    return test_code
```

#### 1.6 Code Execution in Subprocess
- [ ] Create `SubprocessRunner` in `sandbox/subprocess_runner.py`
- [ ] Execute pytest tests and capture stdout/stderr
- [ ] Parse pytest output (passed/failed counts)
- [ ] Implement timeout enforcement (5 minutes max)

#### 1.7 Basic Code Synthesis
- [ ] Implement `ArchitectAgent` in `agents/architect.py`
- [ ] Generate implementation code from test suite
- [ ] Use FastMCP decorator template
- [ ] Validate syntax with AST parsing
- [ ] Implement iterative refinement on test failures

#### 1.8 End-to-End Flow (Manual)
- [ ] Wire Planner → Auditor → Architect → Runner
- [ ] Implement "Red-Green-Refactor" loop
- [ ] Log each step with rich console output
- [ ] Save successful tools to `tools/` directory

**Milestone:** Given prompt "Create a Fibonacci function", system generates `test_fibonacci.py`, fails it (Red), generates `fibonacci.py`, passes it (Green).

---

## Phase 2: Semantic Memory (Vector Database)

**Goal:** Enable tool discovery to prevent duplication and enable tool reuse.

**Duration:** 1-2 weeks

### Tasks

#### 2.1 Vector Database Setup
- [ ] Install ChromaDB (local persistent mode)
- [ ] Create `VectorStore` class in `memory/vector_store.py`
- [ ] Initialize collection with metadata schema
- [ ] Implement CRUD operations (add, query, delete)

```python
# memory/vector_store.py
import chromadb
from chromadb.config import Settings

class VectorStore:
    def __init__(self, persist_directory: str = "./data/vector_db"):
        self.client = chromadb.Client(Settings(
            persist_directory=persist_directory,
            anonymized_telemetry=False
        ))
        self.collection = self.client.get_or_create_collection(
            name="tool_library",
            metadata={"hnsw:space": "cosine"}
        )

    def add_tool(self, tool_id: str, code: str, docstring: str,
                 signature: str, metadata: dict):
        """Index a new tool"""
        embedding_text = f"{docstring}\n\n{signature}"
        self.collection.add(
            ids=[tool_id],
            documents=[embedding_text],
            metadatas=[{
                "code": code,
                "signature": signature,
                **metadata
            }]
        )
```

#### 2.2 Embedding Generation
- [ ] Implement `EmbeddingGenerator` in `memory/embeddings.py`
- [ ] Integrate `sentence-transformers` with `all-MiniLM-L6-v2`
- [ ] Add batch processing for efficiency
- [ ] Cache embeddings to avoid recomputation

#### 2.3 Hybrid Search Implementation
- [ ] Implement BM25 sparse retrieval in `memory/hybrid_search.py`
- [ ] Create Reciprocal Rank Fusion (RRF) for result merging
- [ ] Add re-ranking by similarity score
- [ ] Implement query expansion (synonyms for code terms)

```python
# memory/hybrid_search.py (excerpt)
from rank_bm25 import BM25Okapi

class HybridSearch:
    def search(self, query: str, top_k: int = 5) -> List[dict]:
        # Dense retrieval (vector search)
        vector_results = self.vector_store.query(query, n_results=top_k*2)

        # Sparse retrieval (BM25)
        bm25_results = self.bm25_index.get_top_n(query, top_k*2)

        # Reciprocal Rank Fusion
        fused_results = self._reciprocal_rank_fusion(
            vector_results, bm25_results
        )

        return fused_results[:top_k]
```

#### 2.4 The Librarian Agent
- [ ] Implement `LibrarianAgent` in `agents/librarian.py`
- [ ] Query tool library before each synthesis
- [ ] Implement similarity threshold (0.85 for exact match)
- [ ] Return tool metadata with usage examples
- [ ] Add tool versioning support

#### 2.5 Tool Registration Hook
- [ ] Create `ToolRegistry` in `registry/tool_registry.py`
- [ ] Hook into successful test completion
- [ ] Extract docstring and signature via AST
- [ ] Generate embeddings and store in vector DB
- [ ] Save tool metadata as JSON

```python
# registry/tool_registry.py
class ToolRegistry:
    def register_tool(self, tool_id: str, source_path: str,
                      test_results: dict):
        """Register a verified tool"""
        # Parse source code
        module_ast = ast.parse(source_path.read_text())
        function_def = self._extract_function(module_ast)

        # Extract metadata
        metadata = {
            "name": function_def.name,
            "docstring": ast.get_docstring(function_def),
            "signature": self._build_signature(function_def),
            "test_results": test_results,
            "created_at": datetime.now().isoformat()
        }

        # Store in vector DB
        self.librarian.index_tool(tool_id, metadata)
```

#### 2.6 Integration with Planner
- [ ] Modify Planner to query Librarian first
- [ ] Skip synthesis if exact match found (score > 0.85)
- [ ] Implement "Adapter Agent" for schema mapping
- [ ] Fall back to synthesis if no match found

**Milestone:** Requesting "Calculate Fibonacci sequence" a second time retrieves the existing tool instead of generating new code.

---

## Phase 3: Dynamic Runtime and Hot-Reloading

**Goal:** Enable usage of new tools without restarting the MCP server.

**Duration:** 1-2 weeks

### Tasks

#### 3.1 Watchdog Integration
- [ ] Install `watchdog` library
- [ ] Create `FileSystemObserver` in `registry/hot_reload.py`
- [ ] Monitor `./tools/` directory for `.py` file additions
- [ ] Implement event handlers for create/modify/delete

```python
# registry/hot_reload.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ToolFileHandler(FileSystemEventHandler):
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def on_created(self, event):
        if event.src_path.endswith('.py'):
            self.registry.hot_load_tool(event.src_path)
```

#### 3.2 Dynamic Module Loading
- [ ] Implement `hot_load_tool()` in `registry/tool_registry.py`
- [ ] Use `importlib.util.spec_from_file_location()`
- [ ] Invalidate import cache with `importlib.reload()`
- [ ] Handle import errors gracefully
- [ ] Update FastMCP internal registry

```python
# registry/tool_registry.py (hot-loading)
import importlib.util
import sys

def hot_load_tool(self, tool_path: str):
    """Dynamically load a tool module at runtime"""
    module_name = Path(tool_path).stem

    # Load module from file
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # Extract FastMCP tool decorator
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if hasattr(attr, '__mcp_tool__'):
            self.mcp_instance.register_tool(attr)

    # Notify clients
    self.notify_list_changed()
```

#### 3.3 MCP listChanged Notification
- [ ] Implement `notify_list_changed()` in `registry/mcp_notifier.py`
- [ ] Send JSON-RPC `notifications/tools/list_changed`
- [ ] Ensure notification sent over active WebSocket/stdio
- [ ] Log notification delivery status

```python
# registry/mcp_notifier.py
class MCPNotifier:
    async def notify_list_changed(self):
        """Send listChanged notification to clients"""
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {}
        }
        await self.mcp_server.send_notification(notification)
```

#### 3.4 Tool Persistence Strategy
- [ ] Save successful tools to `./tools/` immediately
- [ ] Use atomic file writes (write to temp, then rename)
- [ ] Include metadata header in generated files
- [ ] Implement tool versioning (semantic versioning)

#### 3.5 Client Compatibility Testing
- [ ] Test with Claude Desktop client
- [ ] Test with MCP Inspector
- [ ] Implement fallback: expose `refresh_tools()` manual tool
- [ ] Document client requirements

#### 3.6 Error Handling and Rollback
- [ ] Detect failed imports and quarantine bad tools
- [ ] Implement rollback mechanism (restore previous version)
- [ ] Send error notifications to user via sampling
- [ ] Log all errors to structured logs

**Milestone:** Dropping a valid `.py` tool into `./tools/` causes it to immediately appear in the MCP client's tool list without server restart.

---

## Phase 4: Hardening and Feedback (Production Ready)

**Goal:** Secure execution and close the feedback loop for self-healing.

**Duration:** 2-3 weeks

### Tasks

#### 4.1 Docker Sandbox Implementation
- [ ] Install Docker SDK for Python
- [ ] Create `DockerRunner` in `sandbox/docker_runner.py`
- [ ] Build minimal Python base image (Alpine-based)
- [ ] Mount `./tools/` as read-only volume
- [ ] Configure network isolation (none/custom bridge)

```python
# sandbox/docker_runner.py
import docker

class DockerRunner:
    def __init__(self):
        self.client = docker.from_env()
        self.image = "python:3.11-alpine"

    def run_test(self, test_file: str, timeout: int = 300) -> dict:
        """Execute test in isolated container"""
        container = self.client.containers.run(
            self.image,
            command=f"pytest {test_file} -v",
            volumes={
                './tools': {'bind': '/app/tools', 'mode': 'ro'},
                './tests': {'bind': '/app/tests', 'mode': 'ro'}
            },
            network_mode='none',  # No internet access
            mem_limit='512m',
            cpu_quota=50000,
            detach=True
        )

        # Wait with timeout
        exit_code = container.wait(timeout=timeout)
        logs = container.logs().decode('utf-8')
        container.remove()

        return {'exit_code': exit_code, 'logs': logs}
```

#### 4.2 Resource Quotas (cgroups)
- [ ] Implement CPU limits (50% of 1 core)
- [ ] Implement memory limits (512MB default)
- [ ] Implement execution timeout (5 minutes)
- [ ] Implement disk I/O limits
- [ ] Log resource usage metrics

#### 4.3 Network Policy Enforcement
- [ ] Default: No network access (`network_mode='none'`)
- [ ] Parse tool requirements for network needs
- [ ] Create allow-list for specific domains
- [ ] Implement DNS-based filtering
- [ ] Log all network access attempts

#### 4.4 MCP Sampling for User Approval
- [ ] Implement `SamplingAgent` in `feedback/sampling.py`
- [ ] Trigger before final tool registration
- [ ] Show user: source code, test results, resource requirements
- [ ] Parse user response (approve/reject/modify)
- [ ] Store approval audit trail

```python
# feedback/sampling.py
class SamplingAgent:
    async def request_approval(self, tool_metadata: dict) -> bool:
        """Request user approval via MCP sampling"""
        message = f"""
I have generated a new tool: `{tool_metadata['name']}`

**Description:** {tool_metadata['docstring']}

**Test Results:** {tool_metadata['test_results']}

**Source Code:**
```python
{tool_metadata['source_code']}
```

Do you approve this tool for registration? (yes/no)
"""

        response = await self.mcp_server.create_message(
            role="assistant",
            content=message
        )

        return response.content.lower().startswith('yes')
```

#### 4.5 The Janitor Process
- [ ] Implement `JanitorAgent` in `feedback/janitor.py`
- [ ] Schedule nightly regression tests (cron/APScheduler)
- [ ] Run all tool test suites against current code
- [ ] Flag failed tools as "Broken"
- [ ] Trigger self-healing workflow

```python
# feedback/janitor.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class JanitorAgent:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(
            self.run_regression_suite,
            'cron',
            hour=2,  # 2 AM daily
            id='regression_suite'
        )

    async def run_regression_suite(self):
        """Run all tool tests and identify broken tools"""
        tools = self.registry.get_all_tools()

        for tool in tools:
            result = await self.warden.run_test(tool.test_path)

            if result['exit_code'] != 0:
                # Tool is broken
                await self.handle_broken_tool(tool, result['logs'])
```

#### 4.6 Self-Healing Workflow
- [ ] Parse test failure stack traces
- [ ] Feed error to Architect with original source
- [ ] Generate patched version
- [ ] Re-run tests in sandbox
- [ ] If successful, hot-reload patched tool
- [ ] Notify user of automatic fix

#### 4.7 User Feedback Integration
- [ ] Expose `report_error()` MCP tool
- [ ] Store user corrections in feedback database
- [ ] Use as negative examples in future synthesis
- [ ] Update test suites to include user edge cases
- [ ] Implement feedback-driven re-ranking

#### 4.8 Production Database Migration
- [ ] Migrate from ChromaDB to pgvector (PostgreSQL)
- [ ] Set up connection pooling
- [ ] Implement backup strategy
- [ ] Add database migrations (Alembic)
- [ ] Optimize indexes for hybrid search

#### 4.9 Observability and Monitoring
- [ ] Integrate Prometheus metrics
- [ ] Track: tool generation rate, test pass rate, sandbox failures
- [ ] Set up alerting for critical failures
- [ ] Implement distributed tracing (OpenTelemetry)
- [ ] Create Grafana dashboards

#### 4.10 Firecracker MicroVM Integration (Optional)
- [ ] Install Firecracker binary
- [ ] Create `FirecrackerRunner` in `sandbox/firecracker_runner.py`
- [ ] Configure KVM virtualization
- [ ] Build minimal guest kernel
- [ ] Benchmark vs. Docker performance

**Milestone:** A secure, self-healing, user-verified agentic ecosystem running in production with full observability.

---

## Phase 5: Advanced Features (Future Work)

### 5.1 WebAssembly Sandbox
- [ ] Compile Python to WASM using Pyodide
- [ ] Implement Wasmtime runner
- [ ] Configure capability-based security
- [ ] Benchmark performance vs. Docker

### 5.2 Multi-Language Support
- [ ] Add TypeScript code generation
- [ ] Add Rust code generation (for performance-critical tools)
- [ ] Implement language detection from task requirements

### 5.3 Collaborative Learning
- [ ] Share tool library across multiple agent instances
- [ ] Implement federated learning for tool ranking
- [ ] Create public tool marketplace

### 5.4 Visual Programming Interface
- [ ] Build web UI for DAG visualization
- [ ] Real-time task execution monitoring
- [ ] Manual intervention and approval workflow

---

## Key Technical Decisions

### 1. Why FastMCP over TypeScript SDK?
- **Justification:** Python offers superior integration with ML/AI tooling (transformers, chromadb), better async support for heavy LLM calls, and simpler dynamic code execution.
- **Tradeoff:** TypeScript has better type safety and is the primary MCP reference implementation.

### 2. Why ChromaDB → pgvector?
- **Justification:** ChromaDB is excellent for rapid prototyping (embedded, no setup). pgvector offers production-grade reliability, ACID compliance, and SQL querying for complex metadata filters.
- **Migration Path:** Phase 2-3 use ChromaDB, Phase 4 migrates to pgvector.

### 3. Why Docker instead of Firecracker initially?
- **Justification:** Docker has better cross-platform support, easier setup, and sufficient isolation for initial development. Firecracker offers superior security but adds operational complexity.
- **Path:** Phase 1-3 use Docker, Phase 4 adds Firecracker as optional backend.

### 4. Embedding Model Selection
- **Phase 2-3:** `all-MiniLM-L6-v2` (fast, local, no API costs)
- **Phase 4+:** Evaluate `voyage-code-2` or `CodeBERT` for improved code semantics
- **Justification:** Start with speed, optimize for accuracy later based on retrieval metrics.

### 5. Test Framework: pytest vs. unittest
- **Decision:** pytest
- **Justification:** Better fixture support, cleaner syntax, rich plugin ecosystem (pytest-asyncio, pytest-timeout), industry standard.

---

## Risk Mitigation

| Risk | Mitigation Strategy |
|------|---------------------|
| **Hallucinated Tests** | Implement "Critic" sub-agent to validate test quality; require tests to fail against empty implementation (Red state verification) |
| **Infinite Recursion** | Enforce maximum decomposition depth (5 levels); detect cycles in DAG |
| **Resource Exhaustion** | Strict cgroups limits; kill containers after timeout; rate limit tool generation |
| **Code Injection** | Never use `eval()`; parse code with AST; run all execution in sandboxes |
| **API Rate Limits** | Implement exponential backoff; cache LLM responses; use smaller models for non-critical tasks |
| **Tool Proliferation** | Enforce high similarity threshold (0.85) before creating new tools; periodic deduplication |
| **Sandbox Escape** | Use hardware virtualization (Firecracker); regular security audits; least-privilege principle |

---

## Success Metrics

### Phase 1
- [ ] Successfully decompose 10 varied user requests into valid DAGs
- [ ] Generate passing test suites for 8/10 requests
- [ ] Achieve 80% test pass rate on first synthesis attempt

### Phase 2
- [ ] Retrieve existing tool with >0.85 similarity for 70% of duplicate requests
- [ ] Reduce redundant tool creation by 50%
- [ ] Query latency <200ms for semantic search

### Phase 3
- [ ] Hot-reload tools in <500ms
- [ ] Zero server downtime during tool addition
- [ ] Successfully notify Claude Desktop client of new tools

### Phase 4
- [ ] Zero sandbox escape incidents
- [ ] 95% user approval rate for generated tools
- [ ] Self-healing success rate >60% for broken tools
- [ ] Average tool generation time <30 seconds end-to-end

---

## Dependencies

### Core
```toml
[project]
dependencies = [
    "fastmcp>=0.4.0",
    "anthropic>=0.18.0",
    "pydantic>=2.5.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-timeout>=2.2.0",
]
```

### Phase 2 (Semantic Memory)
```toml
[project.optional-dependencies]
semantic = [
    "chromadb>=0.4.22",
    "sentence-transformers>=2.3.0",
    "rank-bm25>=0.2.2",
]
```

### Phase 3 (Hot-Reload)
```toml
hotreload = [
    "watchdog>=4.0.0",
]
```

### Phase 4 (Production)
```toml
production = [
    "docker>=7.0.0",
    "psycopg[binary]>=3.1.0",
    "pgvector>=0.2.5",
    "apscheduler>=3.10.0",
    "prometheus-client>=0.19.0",
]
```

---

## Development Workflow

### 1. Environment Setup
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create project
uv init autopoietic-agent
cd autopoietic-agent

# Install dependencies
uv add fastmcp anthropic pydantic pytest

# Activate virtual environment
source .venv/bin/activate
```

### 2. Run Development Server
```bash
# Run with MCP Inspector
uv run mcp dev src/orchestrator.py

# Or with Claude Desktop (add to config)
uv run python -m src.orchestrator
```

### 3. Testing
```bash
# Run unit tests
uv run pytest tests/unit -v

# Run integration tests
uv run pytest tests/integration -v

# Run with coverage
uv run pytest --cov=src tests/
```

### 4. Monitoring
```bash
# View logs
tail -f data/logs/orchestrator.log

# Monitor vector DB
uv run python scripts/inspect_vector_db.py

# Run regression suite manually
uv run python scripts/run_regression.py
```

---

## Next Steps

1. **Review and Approve Plan**: Ensure alignment with project goals
2. **Set Up Development Environment**: Install tools and dependencies
3. **Begin Phase 1 Implementation**: Start with project scaffold
4. **Iterate and Refine**: Adapt plan based on learnings

---

## References

This plan is based on the architectural specification "The Autopoietic Agent: Architectural Specifications for a Self-Propagating Model Context Protocol Layer" and incorporates best practices from:

- MCP Specification (modelcontextprotocol.io)
- FastMCP Documentation
- Test-Driven Development with AI (readysetcloud.io)
- LLM Agents for Code Search (arXiv:2408.11058)
- Reflexion: Language Agents with Verbal Reinforcement Learning
- AWS Firecracker Architecture

---

**Document Version:** 1.0
**Last Updated:** 2025-11-18
**Status:** Ready for Implementation
