# Weave Quickstart

## Install
Python 3.11+ required. Docker is only needed for `SANDBOX_TYPE=docker`.

```bash
# uv
uv venv && uv pip install -e ".[dev]"
# or venv (Windows: .venv\Scripts\activate)
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env     # set ANTHROPIC_API_KEY
```

## Verify without an API key
```bash
pytest -m "not live and not docker"
```

## Run
```bash
python -m src.orchestrator          # stdio MCP server
npx @modelcontextprotocol/inspector python -m src.orchestrator   # optional inspector
```

## First use
1. Call `generate_tool` with a description, e.g. "validate an email address".
2. With `AUTO_APPROVE_TOOLS=false` the tool waits for approval: `list_pending_approvals`, then `approve_tool`. It then appears in the client's tool list without a restart.
3. Ask for something similar later: the Librarian reuses the stored tool after re-running its tests.
4. `run_regression` re-tests the library; `heal_tool` repairs a broken tool.

## Safer setup
The default `SANDBOX_TYPE=subprocess` is not safe for untrusted input. For anything else:
```bash
pip install -e ".[docker]"
# make sure SANDBOX_DOCKER_IMAGE exists locally and has pytest installed
# .env: SANDBOX_TYPE=docker
```
All settings are listed in README.md. Design background: AUTOPOIETIC_AGENT_PLAN.md.
