# Autopoietic Agent

> A self-propagating Model Context Protocol layer that autonomously generates, tests, and evolves its own capabilities.

## Overview

The Autopoietic Agent is an experimental architecture that reimagines MCP servers not as static tool repositories, but as **living, self-modifying systems** capable of synthesizing new capabilities on demand. By combining Test-Driven Development (TDD), semantic code search, and sandboxed execution, this system creates a "liquid software" environment where tools materialize based on user intent and persist for future reuse.

### Key Innovation

Traditional AI agents are limited by pre-defined tool sets. This architecture breaks that constraint by:

1. **Autonomous Tool Generation**: Decomposes complex requests into atomic functions
2. **Test-First Development**: Generates regression tests *before* implementation code
3. **Semantic Memory**: Vector database indexes all tools for instant retrieval
4. **Hot-Reloading**: New tools appear in the client without server restart
5. **Self-Healing**: Automatically patches broken tools when regressions occur

## Architecture

```
User Request → Planner (DAG) → Librarian (Search) → Found? → Use Existing
                                     ↓
                                  Not Found
                                     ↓
                    Auditor (Generate Tests) → Architect (Generate Code)
                                     ↓
                              Warden (Sandbox Test)
                                     ↓
                              Registry (Hot-Load)
                                     ↓
                              Tool Available!
```

### Core Components

| Component | Role | Technology |
|-----------|------|------------|
| **Meta-MCP Orchestrator** | Main server acting as both client and server | FastMCP |
| **Planner** | Decomposes requests into DAGs of subtasks | LLM (Claude) + ReAct |
| **Auditor** | Generates test suites (TDD) | LLM + pytest |
| **Librarian** | Semantic search for existing tools | ChromaDB/pgvector + embeddings |
| **Architect** | Synthesizes implementation code | LLM + AST validation |
| **Warden** | Sandboxed test execution | Docker/Firecracker |
| **Janitor** | Regression testing and self-healing | Scheduler + patch generation |

## Quick Start

### Prerequisites

- Python 3.11+
- Docker Desktop
- Anthropic API key
- 8GB+ RAM

### Installation

```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone repository
git clone https://github.com/yourusername/autopoietic-agent.git
cd autopoietic-agent

# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Run

```bash
# Activate virtual environment
source .venv/bin/activate

# Start MCP server with inspector
uv run mcp dev src/orchestrator.py
```

Open http://localhost:6274 and test the `health_check` tool.

## Documentation

- **[Implementation Plan](./AUTOPOIETIC_AGENT_PLAN.md)** - Detailed phase-by-phase roadmap
- **[Architecture Guide](./ARCHITECTURE.md)** - System design and data flows
- **[Quickstart Guide](./QUICKSTART.md)** - Step-by-step setup
- **[Prompt Templates](./PROMPT_TEMPLATES.md)** - All LLM prompts used

## Implementation Status

### Phase 1: Foundation ⏳ In Progress
- [x] Project structure
- [x] Core data models (TaskNode, TaskGraph)
- [ ] Planner agent (task decomposition)
- [ ] Auditor agent (test generation)
- [ ] Architect agent (code synthesis)
- [ ] Basic subprocess test runner
- [ ] End-to-end demo: Generate Fibonacci function

### Phase 2: Semantic Memory 📋 Planned
- [ ] ChromaDB integration
- [ ] Embedding generation (sentence-transformers)
- [ ] Librarian agent with hybrid search
- [ ] Tool registration hooks
- [ ] Demo: Tool reuse without regeneration

### Phase 3: Hot-Reload 📋 Planned
- [ ] Watchdog file monitoring
- [ ] Dynamic module loading (importlib)
- [ ] MCP `listChanged` notifications
- [ ] Demo: Live tool updates without restart

### Phase 4: Production 📋 Planned
- [ ] Docker sandbox with resource limits
- [ ] MCP Sampling for user approval
- [ ] Janitor regression testing
- [ ] Self-healing workflow
- [ ] Migration to pgvector
- [ ] Prometheus metrics

## Example Usage

### Current (Phase 1 Target)

```python
# User request via MCP
"Create a tool that calculates compound interest"

# System workflow:
1. Planner decomposes → Single task: calculate_compound_interest
2. Auditor generates test suite (5 tests covering edge cases)
3. Architect generates implementation
4. Warden runs tests in Docker sandbox
5. ✓ Tests pass → Tool registered
6. Tool now available for immediate use
```

### Future (Phase 4)

```python
# First request
"Fetch my GitHub stars count"
→ Generates fetch_github_stars() tool (30 seconds)

# Second request (1 hour later)
"Get my GitHub stars"
→ Retrieves existing tool (0.5 seconds) ← 60x faster!

# Tool breaks (API change)
# Janitor detects failure at 2 AM
→ Auto-patches code based on new API
→ Notifies user: "Auto-fixed fetch_github_stars"
```

## Project Structure

```
autopoietic-agent/
├── src/
│   ├── orchestrator.py          # Main MCP server
│   ├── agents/                  # Agent implementations
│   ├── core/                    # DAG, state management
│   ├── synthesis/               # Code/test generation
│   ├── memory/                  # Vector DB, search
│   ├── sandbox/                 # Execution isolation
│   ├── registry/                # Tool management
│   └── feedback/                # Self-healing
├── tools/                       # Auto-generated tools
├── tests/                       # Test suites
├── data/                        # Vector DB, logs
└── pyproject.toml               # Dependencies
```

## Development

### Setup Development Environment

```bash
# Install dev dependencies
uv sync --all-extras

# Install pre-commit hooks
pre-commit install

# Run tests
pytest tests/ -v

# Type checking
mypy src/

# Linting
ruff check src/
```

### Adding a New Agent

```python
# src/agents/my_agent.py
from src.core.message_bus import MessageBus

class MyAgent:
    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.bus.subscribe("event_type", self.handle_event)

    async def handle_event(self, message):
        # Process event
        result = await self.do_work(message.payload)

        # Publish result
        await self.bus.publish("result_ready", result)
```

## Security

This system executes LLM-generated code. Security is critical:

- **Layer 1**: AST parsing (never `eval()`)
- **Layer 2**: Static analysis (Bandit)
- **Layer 3**: Docker/Firecracker isolation
- **Layer 4**: Network isolation (deny-by-default)
- **Layer 5**: Human-in-the-loop approval (MCP Sampling)

See [Architecture Guide](./ARCHITECTURE.md#security-architecture) for details.

## Performance

| Metric | Target | Notes |
|--------|--------|-------|
| Cold path (new tool) | < 60s | Full generation + testing |
| Hot path (existing tool) | < 1s | Semantic retrieval |
| Semantic search | < 200ms | Vector DB query |
| Hot-reload | < 500ms | File watch → import → notify |

## Roadmap

- **v0.1** (Current): Phase 1 - Foundation with TDD
- **v0.2**: Phase 2 - Semantic memory and tool reuse
- **v0.3**: Phase 3 - Hot-reloading and live updates
- **v0.4**: Phase 4 - Production hardening and self-healing
- **v1.0**: Full autonomous operation with monitoring

## Research Background

This architecture synthesizes ideas from:

- **Model Context Protocol** (Anthropic) - Universal AI interoperability
- **Test-Driven Development with AI** - Grounding LLM outputs
- **Reflexion** - Self-correcting agents via verbal feedback
- **Semantic Code Search** - Vector embeddings for code retrieval
- **Firecracker** (AWS) - Secure microVM sandboxing

See the [architectural specification document](./AUTOPOIETIC_AGENT_SPEC.md) for full citations.

## Contributing

This is an experimental research project. Contributions welcome!

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - See [LICENSE](LICENSE) for details.

## Citation

If you use this work in research, please cite:

```bibtex
@software{autopoietic_agent_2025,
  title = {Autopoietic Agent: A Self-Propagating Model Context Protocol Layer},
  author = {Your Name},
  year = {2025},
  url = {https://github.com/yourusername/autopoietic-agent}
}
```

## Acknowledgments

- Anthropic for Claude and the Model Context Protocol
- The FastMCP project for excellent MCP SDK
- AWS Firecracker team for secure sandboxing
- The broader AI agents research community

## Contact

- **Issues**: [GitHub Issues](https://github.com/yourusername/autopoietic-agent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/autopoietic-agent/discussions)
- **Email**: your.email@example.com

---

**Status**: 🚧 Under active development (Phase 1)

**Warning**: This is experimental research software. Do not use in production without thorough security review.
