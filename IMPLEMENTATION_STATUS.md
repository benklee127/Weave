# Implementation Status

Current source of truth (with README.md). Historical snapshots: IMPLEMENTATION_SUMMARY.md, PHASE_2_SUMMARY.md.

## Phase 1: Foundation - done
- Pydantic schemas with `TaskGraph` (topological order, cycle validation, failure propagation) and `ToolStatus`.
- `LLMClient` with `LLMClientProtocol`, per-agent token/cost attribution and optional budget.
- Planner, Auditor, Architect with robust JSON extraction (`src/utils/json_extract.py`) and unique generated file names.
- Warden with a `SandboxRunner` abstraction and `run_red_check` (tests must fail against a stub).

## Phase 2: Semantic memory - done
- ChromaDB `VectorStore` with upsert and lifecycle metadata (status, version, source hash, counters, timestamps).
- Embeddings: sentence-transformers if installed, otherwise deterministic `HashEmbeddingGenerator`.
- `ToolIndexer` (idempotent, version bump on changed source, schemas from type hints).
- Librarian: search excludes non-active tools, `verify_match`, `record_outcome`, `set_status`, `deprecate_duplicates`.

## Phase 3: Hot-reload - done
- `ToolRegistry`: isolated import, static policy, MCP registration, call counters, `tools/list_changed` (best effort).
- `ToolWatcher` (watchdog, debounced); it only loads approved tools.
- Existing approved tools are loaded at startup.

## Phase 4: Production - done, with caveats
- Sandbox: `SubprocessRunner` and `DockerRunner` (network none, read-only fs, resource limits, non-root).
- Approval gate: auto, MCP sampling/elicitation, or pending queue (`approve_tool`, `reject_tool`, `list_pending_approvals`).
- Janitor: `run_regression`, `heal`, `deprecate_duplicates`, thread scheduler (`REGRESSION_INTERVAL_SECONDS`).
- `FeedbackStore` feeds ratings into librarian outcomes and flags low-rated tools.
- `orchestrator.main()` builds the app, loads existing tools, starts the watcher when `HOT_RELOAD` is set, and schedules the Janitor when the interval is above 0.

## Not built / known limitations
- Firecracker sandbox, pgvector backend, Prometheus metrics.
- Subprocess sandbox is not a security boundary; Docker mode is container-level isolation only.
- Loaded tools run inside the server process; the static policy is best effort.
- Live-API (`-m live`) and Docker (`-m docker`) tests are opt-in and not run in CI.
- The Docker sandbox image must already be present locally and contain pytest.

## Testing
`pytest -m "not live and not docker"` runs offline using a scripted `FakeLLMClient` (`tests/fakes.py`) and hash embeddings.
