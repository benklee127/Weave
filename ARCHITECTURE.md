# Autopoietic Agent: System Architecture

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      Upstream MCP Client                         │
│            (Claude Desktop / IDE / Custom Runtime)               │
└────────────────────────┬────────────────────────────────────────┘
                         │ MCP Protocol (JSON-RPC)
                         │ WebSocket / stdio
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Meta-MCP Orchestrator                          │
│                   (FastMCP Server Instance)                      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Message Bus (Event-Driven)                  │   │
│  └────┬─────────┬─────────┬─────────┬─────────┬────────┬──┘   │
│       │         │         │         │         │        │        │
│  ┌────▼───┐ ┌──▼────┐ ┌──▼────┐ ┌──▼─────┐ ┌▼─────┐ ┌▼─────┐ │
│  │Planner │ │Auditor│ │Librar-│ │Archi-  │ │Warden│ │Janitor│ │
│  │ (DAG)  │ │(TDD)  │ │ian    │ │tect    │ │(Exec)│ │(Heal)│ │
│  └────┬───┘ └──┬────┘ └──┬────┘ └──┬─────┘ └┬─────┘ └┬─────┘ │
└───────┼────────┼─────────┼─────────┼────────┼────────┼────────┘
        │        │         │         │        │        │
        ▼        ▼         ▼         ▼        ▼        ▼
┌───────────────────────────────────────────────────────────────┐
│                     Shared Data Layer                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │  Task Graph  │  │  Vector DB   │  │  Tool Registry│       │
│  │   (State)    │  │ (ChromaDB/   │  │  (Dynamic)    │       │
│  │              │  │  pgvector)   │  │              │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└───────────────────────────────────────────────────────────────┘
        │                    │                   │
        ▼                    ▼                   ▼
┌───────────────────────────────────────────────────────────────┐
│                    External Services                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Claude API   │  │   Docker     │  │  Filesystem  │        │
│  │  (LLM)       │  │  Sandbox     │  │  (./tools/)  │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└───────────────────────────────────────────────────────────────┘
```

---

## Component Interaction Flow

### Flow 1: New Tool Creation (Cold Path)

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. PLANNER: Decompose into DAG                              │
│    Input:  "Plot API latency trends"                        │
│    Output: [fetch_logs, parse_metrics, plot_chart]         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. LIBRARIAN: Semantic Search (per subtask)                │
│    Query:  "fetch_logs" embedding                           │
│    Result: No match found (score < 0.85)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. AUDITOR: Generate Test Suite                            │
│    Input:  Task spec (fetch_logs)                          │
│    Output: tests/generated/test_fetch_logs.py              │
│            - test_happy_path()                              │
│            - test_empty_response()                          │
│            - test_invalid_date_range()                      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. AUDITOR (Critic): Validate Test Quality                 │
│    Checks: - Assertion density > 0.3                        │
│            - Tests fail against empty implementation        │
│            - No vacuous assertions (assert True)            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. ARCHITECT: Generate Implementation                       │
│    Input:  Test suite + task spec                          │
│    Output: tools/fetch_logs.py                             │
│            @mcp.tool() decorated function                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. WARDEN: Execute in Sandbox                              │
│    Action: docker run python:alpine pytest test_fetch_logs │
│    Result: ✓ 3/3 tests passed                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. SAMPLING: Request User Approval (Optional)              │
│    Show:   Source code, test results, docstring            │
│    User:   "Approve" or "Reject" or "Modify"               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. REGISTRY: Register & Hot-Load                           │
│    Actions:                                                 │
│    - Save to ./tools/fetch_logs.py                         │
│    - Generate embeddings                                    │
│    - Index in vector DB                                     │
│    - importlib.reload()                                     │
│    - Send notifications/tools/list_changed                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
                  Tool Available!
```

### Flow 2: Tool Reuse (Hot Path)

```
User Request: "Fetch API logs from yesterday"
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. PLANNER: Decompose                                       │
│    Output: [fetch_logs(date='yesterday')]                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. LIBRARIAN: Semantic Search                              │
│    Query:  "fetch logs from date range"                     │
│    Result: MATCH! tools/fetch_logs.py (score: 0.92)        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. ADAPTER: Map Request to Tool Schema                     │
│    Input:  "yesterday"                                      │
│    Map:    date='yesterday' → start_date='2025-11-17'      │
│    Output: tools/call payload                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. EXECUTE: Call Existing Tool                             │
│    Result: [logs returned]                                  │
└─────────────────────────────────────────────────────────────┘

Total time: <1 second (vs. 30+ seconds for cold path)
```

### Flow 3: Self-Healing (Janitor)

```
Scheduled: 2:00 AM Daily
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. JANITOR: Enumerate All Tools                            │
│    Query:  SELECT * FROM tool_registry                      │
│    Result: 47 tools found                                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. WARDEN: Run Regression Tests (per tool)                 │
│    Test:   tools/fetch_stock_price.py                      │
│    Result: ✗ FAILED - API endpoint changed                 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. JANITOR: Flag as Broken                                 │
│    Action: UPDATE tool_registry SET status='broken'         │
│    Log:    Stack trace and error message                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. ARCHITECT: Generate Patch                               │
│    Input:  - Original source                                │
│            - Test suite                                     │
│            - Error trace                                    │
│    Prompt: "Fix this code to pass the test. Error: ..."    │
│    Output: Patched source code                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. WARDEN: Verify Patch                                    │
│    Test:   Run regression tests again                       │
│    Result: ✓ 5/5 tests passed                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. REGISTRY: Hot-Reload Patched Tool                       │
│    Action: Replace tools/fetch_stock_price.py              │
│    Notify: User via sampling ("Auto-fixed broken tool")    │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Models

### TaskNode (core/task_state.py)

```python
from pydantic import BaseModel
from typing import List, Optional, Dict
from enum import Enum

class TaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"

class TaskNode(BaseModel):
    id: str
    description: str
    state: TaskState
    dependencies: List[str]
    input_schema: Dict
    output_schema: Dict
    test_suite_path: Optional[str] = None
    implementation_path: Optional[str] = None
    error_trace: Optional[str] = None
    created_at: str
    updated_at: str
```

### ToolMetadata (registry/tool_registry.py)

```python
class ToolMetadata(BaseModel):
    id: str
    name: str
    description: str
    signature: str
    source_code: str
    docstring: str
    input_schema: Dict
    output_schema: Dict
    test_results: Dict
    vector_id: str
    embedding: Optional[List[float]] = None
    version: str  # Semantic versioning
    status: str  # "active", "broken", "deprecated"
    created_at: str
    last_verified_at: str
    usage_count: int
    success_rate: float
```

### TestSuiteMetadata (synthesis/test_generator.py)

```python
class TestSuiteMetadata(BaseModel):
    task_id: str
    test_file_path: str
    test_functions: List[str]
    assertion_count: int
    assertion_density: float
    coverage_estimate: float
    validation_status: str  # "validated", "failed_critic", "pending"
    generated_at: str
```

---

## Agent Communication Protocols

### Message Bus Events

All agents communicate via a central event bus using this message format:

```python
class AgentMessage(BaseModel):
    event_type: str  # "task_created", "test_generated", "tool_registered", etc.
    sender: str      # Agent ID
    payload: Dict
    timestamp: str
    correlation_id: str  # For tracing related events
```

**Event Types:**

1. **task_decomposed** (Planner → All)
   - Payload: `{task_graph: TaskGraph}`

2. **semantic_search_complete** (Librarian → Planner)
   - Payload: `{task_id: str, match_found: bool, tool_id: Optional[str], similarity: float}`

3. **test_suite_generated** (Auditor → Architect)
   - Payload: `{task_id: str, test_path: str, validation_status: str}`

4. **code_synthesized** (Architect → Warden)
   - Payload: `{task_id: str, source_path: str}`

5. **test_execution_complete** (Warden → Registry)
   - Payload: `{task_id: str, success: bool, results: Dict}`

6. **tool_registered** (Registry → All)
   - Payload: `{tool_id: str, metadata: ToolMetadata}`

7. **tool_broken** (Janitor → Architect)
   - Payload: `{tool_id: str, error_trace: str}`

---

## Security Architecture

### Defense-in-Depth Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Input Validation                                   │
│ - JSON schema validation on all user inputs                 │
│ - Rate limiting on tool generation requests                 │
│ - Prompt injection detection                                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Static Analysis                                    │
│ - AST parsing (no eval() ever)                              │
│ - Bandit security linting                                   │
│ - Detect dangerous imports (os.system, subprocess.Popen)    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Sandbox Isolation                                  │
│ - Docker with network_mode='none' by default                │
│ - Read-only volume mounts                                   │
│ - cgroups resource limits (CPU, memory, I/O)                │
│ - Execution timeout enforcement                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Runtime Monitoring                                 │
│ - Log all tool executions                                   │
│ - Detect anomalous resource usage                           │
│ - Syscall filtering (seccomp profiles)                      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: Human-in-the-Loop                                  │
│ - User approval via MCP sampling                            │
│ - Audit trail of all generated code                         │
│ - Manual review for sensitive operations                    │
└─────────────────────────────────────────────────────────────┘
```

### Network Policy Matrix

| Tool Category | Network Access | Allowed Domains | Rationale |
|---------------|----------------|-----------------|-----------|
| Data Processing | None | [] | Pure computation, no external dependencies |
| API Clients | Allowlist | ["api.example.com"] | Explicit domain declaration required |
| Database Tools | Localhost | ["localhost:5432"] | Only local connections |
| ML Inference | None | [] | Models run locally |

---

## Performance Characteristics

### Latency Budget (Target)

| Operation | Target | Notes |
|-----------|--------|-------|
| Task Decomposition | < 5s | Single LLM call (Claude Sonnet) |
| Semantic Search | < 200ms | Vector DB query + re-ranking |
| Test Generation | < 10s | LLM call with structured output |
| Code Synthesis | < 15s | LLM call with retry logic |
| Test Execution | < 30s | Includes Docker container startup |
| Hot-Reload | < 500ms | File watch + import + notify |
| **Total (Cold Path)** | **< 60s** | End-to-end new tool creation |
| **Total (Hot Path)** | **< 1s** | Tool reuse from library |

### Throughput Targets

- **Concurrent Tool Generations**: 3 (LLM API limit)
- **Tools in Library**: 1,000+ (vector DB capacity)
- **Regression Tests per Night**: All tools (scheduled off-peak)

### Resource Usage

| Component | CPU | Memory | Disk I/O |
|-----------|-----|--------|----------|
| Orchestrator | 0.5 core | 512 MB | Low |
| Vector DB (ChromaDB) | 0.2 core | 256 MB | Medium |
| Docker Sandbox (each) | 0.5 core | 512 MB | Low |
| LLM API Calls | N/A | N/A | N/A |

---

## Scalability Considerations

### Horizontal Scaling

The architecture supports horizontal scaling by:

1. **Stateless Orchestrator**: Multiple instances can run concurrently
2. **Shared Vector DB**: pgvector with connection pooling
3. **Distributed Task Queue**: Use Celery for task distribution
4. **Load Balancer**: HAProxy for MCP WebSocket connections

### Vertical Scaling Limits

- **Vector DB Size**: ~1M tools (with sharding strategy)
- **Concurrent Sandboxes**: Limited by Docker daemon (typically 100-200)
- **LLM API Rate**: Anthropic tier limits (e.g., 1000 RPM for Pro)

---

## Failure Modes and Recovery

| Failure Scenario | Detection | Recovery Strategy |
|------------------|-----------|-------------------|
| LLM API Timeout | HTTP 408 / timeout exception | Exponential backoff, retry 3x, then queue for later |
| Docker Daemon Down | Connection refused | Graceful degradation: queue tasks, alert ops |
| Vector DB Corruption | Query errors | Restore from backup, rebuild index from tool metadata |
| Sandbox Escape (Detected) | Anomaly detection | Kill container, quarantine tool, alert security team |
| Infinite Loop in Generated Code | Timeout enforcement | Kill process, mark tool as failed, log for analysis |
| Disk Full (./tools/) | OS error on write | Trigger cleanup, archive old tools, alert |

---

## Observability Stack

### Metrics (Prometheus)

```python
# Custom metrics exposed
tool_generation_duration_seconds = Histogram('tool_generation_duration')
tool_generation_total = Counter('tool_generation_total', ['status'])
tool_reuse_rate = Gauge('tool_reuse_rate')
sandbox_execution_failures = Counter('sandbox_execution_failures', ['error_type'])
vector_db_query_duration_seconds = Histogram('vector_db_query_duration')
```

### Logging (Structured JSON)

```json
{
  "timestamp": "2025-11-18T14:32:01Z",
  "level": "INFO",
  "component": "architect",
  "event": "code_synthesized",
  "task_id": "task_abc123",
  "tool_name": "fetch_stock_price",
  "duration_ms": 12340,
  "llm_tokens": 1523,
  "correlation_id": "req_xyz789"
}
```

### Tracing (OpenTelemetry)

End-to-end traces for each user request:
1. Request received
2. Planner decomposition
3. Librarian search
4. Test generation
5. Code synthesis
6. Sandbox execution
7. Tool registration
8. Response sent

---

## Deployment Architecture

### Development

```
Local Machine
├── Orchestrator (Python process)
├── ChromaDB (embedded)
└── Docker Desktop
```

### Production (AWS)

```
ECS Cluster
├── Orchestrator Service (Fargate)
│   ├── Auto-scaling (2-10 instances)
│   └── ALB (WebSocket support)
├── RDS PostgreSQL (pgvector)
│   ├── Multi-AZ
│   └── Read replicas
├── ECS Task Definitions (Sandboxes)
│   └── Firecracker VMs via Fargate
└── CloudWatch Logs + Prometheus
```

---

## Future Architecture Enhancements

### 1. Distributed Tool Library

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Agent A     │    │  Agent B     │    │  Agent C     │
│  (User 1)    │    │  (User 2)    │    │  (User 3)    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Global Tool Registry  │
              │   (Federated Vector)   │
              └────────────────────────┘
```

### 2. Multi-Model Ensemble

- Use Claude for reasoning and decomposition
- Use Codex/StarCoder for code synthesis
- Use smaller models for test validation

### 3. Incremental Learning

- Fine-tune embedding model on successful tool retrievals
- RLHF on user feedback for code generation
- Curriculum learning: easy tasks → complex tasks

---

**Last Updated:** 2025-11-18
**Status:** Design Complete
