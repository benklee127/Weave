# Weave

> An autopoietic MCP server: it decomposes a request into tasks, reuses tools from a vector library or generates tests and code with LLM agents, verifies them in a sandbox, and hot-loads the verified tools into the live server. A Janitor re-tests and heals tools, and a human approval gate sits in front of anything going live.

The source of truth for status is this README and [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). The design rationale is in [AUTOPOIETIC_AGENT_PLAN.md](AUTOPOIETIC_AGENT_PLAN.md). `IMPLEMENTATION_SUMMARY.md` and `PHASE_2_SUMMARY.md` are historical.

## How it works

```
request -> Planner (task DAG)
  per task, in dependency order:
    Librarian: hybrid search -> verify_match (file, hash, signature) -> fresh Warden run of stored tests
       reuse hit  -> done
       otherwise  -> Auditor writes tests -> Critic review -> Warden red check (tests must fail on a stub)
                  -> Architect writes code -> Warden runs tests (refine within one shared iteration budget)
    verified -> approval gate -> Librarian.register_tool + Registry.load_tool (tools/list_changed sent)
  failures propagate to all transitive dependents
Janitor: re-runs stored tests, marks broken tools, heals via Architect.refine_implementation
```

| Component | Role | Module |
|-----------|------|--------|
| Orchestrator | FastMCP server and management tools | `src/orchestrator.py` |
| Pipeline | Scheduling, reuse, TDD loop, approval, registration | `src/pipeline.py` |
| Planner / Auditor / Architect | LLM agents (decompose, tests, code) | `src/agents/` |
| Librarian | Vector + keyword search, lifecycle metadata | `src/agents/librarian.py`, `src/memory/` |
| Warden | Test execution via a sandbox runner | `src/agents/warden.py`, `src/sandbox/` |
| Registry | Isolated import, static policy, MCP registration, file watcher | `src/registry/` |
| Approval | Auto, MCP sampling/elicitation, or pending queue | `src/approval.py` |
| Janitor / Feedback | Regression, healing, user ratings | `src/feedback/` |

## Quick start

Requires Python 3.11+ and an Anthropic API key. Docker is optional (only for `SANDBOX_TYPE=docker`).

```bash
# with uv
uv venv && uv pip install -e ".[dev]"
# or with venv
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env          # set ANTHROPIC_API_KEY
python -m src.orchestrator    # stdio MCP server (also: `weave`)
```

Optional extras: `.[semantic]` (sentence-transformers + BM25; without it a deterministic hash embedding is used), `.[docker]` (Docker sandbox), `.[all]`.

Register with an MCP client, for example Claude Desktop:

```json
{"mcpServers": {"weave": {"command": "C:/path/to/Weave/.venv/Scripts/python.exe", "args": ["-m", "src.orchestrator"], "cwd": "C:/path/to/Weave"}}}
```

## MCP tools

| Tool | Purpose |
|------|---------|
| `health_check` | Status, library stats, loaded tools, token usage |
| `generate_tool(description, max_tasks)` | Run the full pipeline |
| `list_generated_tools(status)` | Library contents by lifecycle status |
| `search_tools(query, n_results)` | Search the library |
| `run_tool(name, args_json)` | Call a loaded tool in-process |
| `list_pending_approvals` / `approve_tool` / `reject_tool` | Approval gate |
| `run_regression` | Re-run all active tools' stored tests |
| `heal_tool(tool_id, max_attempts)` | Patch and re-verify a broken tool |
| `deprecate_tool(tool_id)` | Retire a tool |
| `tool_stats(tool_id)` | Usage and success counters |

Approved generated tools also appear as first-class MCP tools named after their function.

## Configuration

Environment variables (or `.env`); defined in `src/config.py`. Relative paths resolve against the project root.

| Variable | Default | Meaning |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | none | Required for generation (not for import or tests) |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | LLM model |
| `VECTOR_DB_PATH` | `data/vector_db` | ChromaDB directory |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Used when sentence-transformers is installed |
| `SANDBOX_TYPE` | `subprocess` | `subprocess` or `docker` |
| `SANDBOX_TIMEOUT` | `300` | Seconds per test run |
| `SANDBOX_MEMORY_LIMIT` | `512m` | Docker memory limit |
| `SANDBOX_CPU_QUOTA` | `50000` | Docker CPU quota (100000 = one CPU) |
| `SANDBOX_DOCKER_IMAGE` | `python:3.11-slim` | Image used by the Docker sandbox; it must contain pytest |
| `TOOL_DIRECTORY` | `tools` | Generated tool sources |
| `TESTS_DIRECTORY` | `tests/generated` | Generated tests |
| `AUTO_APPROVE_TOOLS` | `false` | Skip the human approval gate |
| `HOT_RELOAD` | `true` | Watch the tools directory |
| `REGRESSION_INTERVAL_SECONDS` | `0` | Scheduled Janitor regression; 0 = off |
| `REUSE_SIMILARITY_THRESHOLD` | `0.75` | Minimum similarity to consider reuse |
| `REUSE_HIGH_CONFIDENCE` | `0.85` | High-confidence match level |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `data/logs/orchestrator.log` | Logging |

## Security

Generated code is untrusted. Layers:

- **Subprocess sandbox (default) is NOT safe for untrusted input.** Tests run in a child process with a scrubbed environment (no API keys), a timeout that kills the process tree, and an output cap, but with full filesystem and network access as your user. Use it only for local development with prompts you trust.
- **Docker sandbox (`SANDBOX_TYPE=docker`)** isolates test runs: no network, read-only root filesystem, memory/CPU/pids limits, non-root user, only the needed files mounted. It shares the host kernel, so it is container-level isolation, not a VM boundary. If Docker is unavailable it fails with a clear error.
- **Static policy** in the registry refuses tools using `exec`/`eval`/`compile`, dynamic `__import__`, `os`/`subprocess`, or socket-level networking. Once loaded, a tool runs inside the server process, outside the sandbox; the policy is a filter, not a boundary.
- **Approval gate**: nothing is loaded into the live server without approval unless `AUTO_APPROVE_TOOLS=true`. The file watcher only loads approved tools. Keep auto-approval off outside development.
- Reuse requires the stored source hash to match and the stored tests to pass again.

## Development

```bash
pytest -m "not live and not docker"      # offline: no API key, no Docker
pytest -m docker                         # needs a running Docker daemon
pytest -m live                           # needs ANTHROPIC_API_KEY, spends tokens
pytest --cov=src                         # coverage is opt-in
ruff check src tests
```

CI runs ruff and the non-live, non-docker tests on Ubuntu and Windows with Python 3.11 to 3.13.

## Status

- [x] Phase 1 Foundation: planner, auditor, architect, warden, TDD loop, DAG scheduling
- [x] Phase 2 Semantic memory: ChromaDB library, hash-embedding fallback, lifecycle metadata, verified reuse
- [x] Phase 3 Hot-reload: registry, `tools/list_changed`, watchdog watcher
- [x] Phase 4 Production features: Docker sandbox, approval gate, Janitor regression and healing, feedback store
- [ ] Not built: Firecracker sandbox, pgvector backend, Prometheus metrics, stronger isolation for loaded tools

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for detail and known limitations.

## License

MIT
