"""
Pipeline tests: a scripted FakeLLMClient drives the real agents, a real
SubprocessRunner, a hash-embedding tmp vector store and a real FastMCP server
with a real ToolRegistry. No network, no API key.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest
from fastmcp import Client, FastMCP

from src.agents.architect import ArchitectAgent
from src.agents.auditor import AuditorAgent
from src.agents.librarian import LibrarianAgent
from src.agents.planner import PlannerAgent
from src.agents.warden import WardenAgent
from src.approval import ApprovalGate
from src.memory.embeddings import HashEmbeddingGenerator
from src.pipeline import Pipeline, bind_test_imports
from src.registry.tool_registry import ToolRegistry
from src.sandbox.subprocess_runner import SubprocessRunner
from tests.fakes import SAMPLE_CRITIC_APPROVE, FakeLLMClient

ADD_DESC = "Add two integers"
ADD_IMPL = 'def add_two_integers(a: int, b: int) -> int:\n    """Add two integers."""\n    return a + b\n'
ADD_BAD_IMPL = 'def add_two_integers(a: int, b: int) -> int:\n    """Add two integers."""\n    return a - b\n'
ADD_TESTS = (
    "from add_two_integers import add_two_integers\n\n\n"
    "def test_basic():\n"
    "    assert add_two_integers(1, 2) == 3\n"
    "    assert add_two_integers(0, 0) == 0\n\n\n"
    "def test_negative():\n"
    "    assert add_two_integers(-1, -2) == -3\n"
    "    assert add_two_integers(-1, 1) == 0\n"
)
VACUOUS_TESTS = (
    "from add_two_integers import add_two_integers\n\n\n"
    "def test_nothing():\n"
    "    assert True\n"
)
DOUBLE_DESC = "Double a number"
DOUBLE_IMPL = 'def double_a_number(x: int) -> int:\n    """Double a number."""\n    return x * 2\n'
DOUBLE_TESTS = (
    "from double_a_number import double_a_number\n\n\n"
    "def test_double():\n"
    "    assert double_a_number(2) == 4\n"
    "    assert double_a_number(-3) == -6\n"
)
CRITIC_REJECT = json.dumps({
    "valid": False, "assertion_count": 1, "total_lines": 5, "assertion_density": 0.2,
    "issues": ["too weak"], "recommendation": "REJECT", "reason": "weak tests",
})
CRITIC_REVISE = CRITIC_REJECT.replace("REJECT", "NEEDS_REVISION")


def task_spec(desc, tests, impl, deps=None):
    return SimpleNamespace(desc=desc, tests=tests, impl=impl, deps=deps or [])


ADD = task_spec(ADD_DESC, ADD_TESTS, ADD_IMPL)
DOUBLE = task_spec(DOUBLE_DESC, DOUBLE_TESTS, DOUBLE_IMPL, deps=[0])


def make_llm(specs: List, critic=SAMPLE_CRITIC_APPROVE, refine: Optional[object] = None):
    """Script planner, critic, test generation, synthesis and refinement."""
    llm = FakeLLMClient()
    if refine is not None:
        llm.add_response("debugging code that failed", refine, agent="architect")
    llm.add_response("test quality auditor", critic, agent="auditor")
    plan = [
        {"description": s.desc, "dependencies": s.deps, "input_schema": {}, "output_schema": {}}
        for s in specs
    ]
    llm.add_response("decompose the user request", json.dumps(plan), agent="planner")
    for s in specs:
        llm.add_response(f"description: {s.desc.lower()}", s.tests, agent="auditor")
        llm.add_response(f"function specification:\n{s.desc.lower()}", s.impl, agent="architect")
    return llm


def build_stack(settings, store, llm, auto_approve=True, max_iterations=3, **gate_kwargs):
    settings.auto_approve_tools = auto_approve
    mcp = FastMCP("test")
    generator = HashEmbeddingGenerator()
    librarian = LibrarianAgent(
        vector_store=store, similarity_threshold=0.3, high_confidence_threshold=0.5,
        embedding_generator=generator,
    )
    architect = ArchitectAgent(llm, tool_output_dir=settings.tool_directory)
    auditor = AuditorAgent(llm, test_output_dir=settings.tests_directory)
    warden = WardenAgent(runner=SubprocessRunner(timeout=120))
    registry = ToolRegistry(mcp, settings.tool_directory)
    gate = ApprovalGate(auto_approve=auto_approve, **gate_kwargs)
    pipeline = Pipeline(
        settings, llm, PlannerAgent(llm), librarian, auditor, architect, warden, registry, gate,
        max_iterations=max_iterations, reserved_names={"health_check"},
    )
    return SimpleNamespace(
        pipeline=pipeline, llm=llm, mcp=mcp, registry=registry, librarian=librarian,
        gate=gate, settings=settings, architect=architect, auditor=auditor, warden=warden,
    )


@pytest.fixture
def make_stack(tmp_settings, tmp_vector_store):
    def _make(llm, **kw):
        return build_stack(tmp_settings, tmp_vector_store, llm, **kw)

    return _make


def count(llm, agent):
    return len(llm.calls_for(agent))


# -- generate + verify + register + callable ---------------------------------

async def test_generate_verify_register_and_call(make_stack):
    s = make_stack(make_llm([ADD]))
    report = s.pipeline.run("add numbers", max_tasks=3)

    assert report["status"] == "completed"
    assert (report["total_tasks"], report["verified"], report["reused"], report["failed"]) == (1, 1, 0, 0)
    res = report["results"][0]
    assert res["status"] == "verified"
    assert res["registered_in_library"] is True
    assert res["loaded"] is True and res["tool_name"] == "add_two_integers"
    assert res["approval"] == "approved"
    assert Path(res["implementation_path"]).is_file() and Path(res["test_path"]).is_file()
    # tests were bound to the real (unique) implementation module
    assert Path(res["implementation_path"]).stem in Path(res["test_path"]).read_text(encoding="utf-8")

    tool = s.librarian.get_tool(res["task_id"])
    assert tool["status"] == "active" and tool["approved"] is True
    assert tool["test_path"] == res["test_path"]

    async with Client(s.mcp) as client:
        assert "add_two_integers" in {t.name for t in await client.list_tools()}
        out = await client.call_tool("add_two_integers", {"a": 20, "b": 22})
        assert out.data == 42


# -- dependency ordering -----------------------------------------------------

def test_dependencies_run_in_order_and_feed_prompts(make_stack):
    # DOUBLE is listed first in the plan but depends on ADD (index 1).
    double_first = task_spec(DOUBLE_DESC, DOUBLE_TESTS, DOUBLE_IMPL, deps=[1])
    s = make_stack(make_llm([double_first, ADD]))
    report = s.pipeline.run("add then double", max_tasks=5)

    assert report["status"] == "completed" and report["verified"] == 2
    arch = [c.prompt for c in s.llm.calls_for("architect")]
    assert len(arch) == 2
    assert "add two integers" in arch[0].lower().split("function specification:")[1][:40]
    assert "double a number" in arch[1].lower().split("function specification:")[1][:40]
    # the downstream prompt carries the upstream tool signature
    assert "AVAILABLE UPSTREAM TOOLS" in arch[1] and "add_two_integers(" in arch[1]
    assert "AVAILABLE UPSTREAM TOOLS" not in arch[0]
    assert [r["description"] for r in report["results"]] == [DOUBLE_DESC, ADD_DESC]


# -- failure propagation ------------------------------------------------------

def test_failure_propagates_to_dependents(make_stack):
    bad = task_spec(ADD_DESC, ADD_TESTS, ADD_BAD_IMPL)
    dependent = task_spec(DOUBLE_DESC, DOUBLE_TESTS, DOUBLE_IMPL, deps=[0])
    s = make_stack(make_llm([bad, dependent], refine=ADD_BAD_IMPL), max_iterations=2)
    report = s.pipeline.run("x", max_tasks=5)

    assert report["status"] == "failed"
    first, second = report["results"]
    assert first["status"] == "failed" and "iteration budget" in first["error"]
    assert second["status"] == "failed" and "Dependency" in second["error"]
    # dependent never reached the LLM: 1 synth + 1 refine for the failed task only
    assert count(s.llm, "architect") == 2
    assert s.registry.list_loaded() == []


def test_partial_status_when_independent_task_succeeds(make_stack):
    bad = task_spec(ADD_DESC, ADD_TESTS, ADD_BAD_IMPL)
    good = task_spec(DOUBLE_DESC, DOUBLE_TESTS, DOUBLE_IMPL)
    s = make_stack(make_llm([bad, good], refine=ADD_BAD_IMPL), max_iterations=1)
    report = s.pipeline.run("x")
    assert report["status"] == "partial"
    assert [r["status"] for r in report["results"]] == ["failed", "verified"]


def test_planner_failure_reports_error(make_stack):
    llm = FakeLLMClient(default="not json at all")
    s = make_stack(llm)
    report = s.pipeline.run("anything")
    assert report["status"] == "error" and report["error"]


# -- reuse -----------------------------------------------------------------

def test_second_identical_request_reuses_without_generation(make_stack):
    s = make_stack(make_llm([ADD]))
    first = s.pipeline.run("add numbers")
    assert first["verified"] == 1
    auditor_calls, architect_calls = count(s.llm, "auditor"), count(s.llm, "architect")

    second = s.pipeline.run("add numbers")
    assert second["status"] == "completed"
    assert (second["reused"], second["verified"]) == (1, 0)
    res = second["results"][0]
    assert res["status"] == "reused" and res["reused_tool_id"] == first["results"][0]["task_id"]
    assert res["loaded"] is True
    # no test/code generation happened the second time
    assert count(s.llm, "auditor") == auditor_calls
    assert count(s.llm, "architect") == architect_calls
    assert s.librarian.get_tool(res["reused_tool_id"])["usage_count"] >= 1


def test_tampered_tool_is_not_reused(make_stack):
    s = make_stack(make_llm([ADD]))
    first = s.pipeline.run("add numbers")
    Path(first["results"][0]["implementation_path"]).write_text(ADD_IMPL + "# edited\n", encoding="utf-8")

    second = s.pipeline.run("add numbers")
    assert second["reused"] == 0 and second["verified"] == 1  # regenerated instead


def test_reuse_requires_fresh_passing_tests(make_stack):
    s = make_stack(make_llm([ADD]))
    first = s.pipeline.run("add numbers")
    tool_id = first["results"][0]["task_id"]
    # Break the stored tests (source hash is untouched, so only the warden run catches it).
    Path(first["results"][0]["test_path"]).write_text(
        ADD_TESTS.replace("== 3", "== 4"), encoding="utf-8"
    )
    second = s.pipeline.run("add numbers")
    assert second["reused"] == 0
    assert s.librarian.get_tool(tool_id)["status"] == "broken"


# -- critic / red check -----------------------------------------------------

def test_critic_rejection_regenerates_once_then_fails(make_stack):
    s = make_stack(make_llm([ADD], critic=CRITIC_REJECT))
    report = s.pipeline.run("x")
    res = report["results"][0]
    assert res["status"] == "failed" and "critic" in res["error"]
    assert count(s.llm, "architect") == 0
    # one generation + one regeneration, each followed by a critic call
    assert count(s.llm, "auditor") == 4


def test_critic_needs_revision_then_approve(make_stack):
    llm = make_llm([ADD], critic=[CRITIC_REVISE, SAMPLE_CRITIC_APPROVE])
    s = make_stack(llm)
    report = s.pipeline.run("x")
    assert report["status"] == "completed" and report["verified"] == 1
    assert count(llm, "auditor") == 4


def test_red_check_failure_blocks_synthesis(make_stack):
    s = make_stack(make_llm([task_spec(ADD_DESC, VACUOUS_TESTS, ADD_IMPL)]))
    report = s.pipeline.run("x")
    res = report["results"][0]
    assert res["status"] == "failed" and "Red check" in res["error"]
    assert count(s.llm, "architect") == 0


# -- iteration budget ------------------------------------------------------

def test_refinement_consumes_shared_budget(make_stack):
    bad_first = task_spec(ADD_DESC, ADD_TESTS, ADD_BAD_IMPL)
    s = make_stack(make_llm([bad_first], refine=ADD_IMPL), max_iterations=3)
    report = s.pipeline.run("x")
    res = report["results"][0]
    assert res["status"] == "verified" and res["attempts"] == 2
    assert count(s.llm, "architect") == 2


def test_exceptions_consume_the_same_budget(make_stack):
    broken_syntax = "def add_two_integers(a, b) return a + b"
    spec = task_spec(ADD_DESC, ADD_TESTS, [broken_syntax, broken_syntax, ADD_IMPL])
    llm = make_llm([spec])
    # rule responses can be a list: replace the single-impl rule by a sequence
    for rule in llm.rules:
        if rule.keyword.startswith("function specification"):
            rule.responses = [broken_syntax, broken_syntax, ADD_IMPL]
    s = make_stack(llm, max_iterations=3)
    ok = s.pipeline.run("x")["results"][0]
    assert ok["status"] == "verified" and ok["attempts"] == 3

    # Same script, budget of 2: the third (good) attempt is never made.
    llm2 = make_llm([spec])
    for rule in llm2.rules:
        if rule.keyword.startswith("function specification"):
            rule.responses = [broken_syntax, broken_syntax, ADD_IMPL]
    s2 = build_stack(s.settings, s.librarian.vector_store, llm2, max_iterations=2)
    # fresh library would reuse; make sure we compare against an empty one
    s2.librarian.vector_store.reset()
    bad = s2.pipeline.run("y")["results"][0]
    assert bad["status"] == "failed" and "iteration budget" in bad["error"]
    assert count(llm2, "architect") == 2


# -- approval gate ---------------------------------------------------------

def test_pending_then_approve_loads_tool(make_stack):
    s = make_stack(make_llm([ADD]), auto_approve=False)
    report = s.pipeline.run("x")
    res = report["results"][0]
    assert res["status"] == "verified" and res["approval"] == "pending" and res["loaded"] is False
    assert s.registry.list_loaded() == []
    pending = s.gate.list_pending()
    assert [p["tool_id"] for p in pending] == [res["task_id"]]
    assert report["pending_approvals"] == pending

    out = s.gate.approve(res["task_id"])
    assert out["status"] == "approved" and out["activation"]["name"] == "add_two_integers"
    assert [e["name"] for e in s.registry.list_loaded()] == ["add_two_integers"]
    assert s.librarian.get_tool(res["task_id"])["approved"] is True
    assert s.gate.list_pending() == []
    with pytest.raises(KeyError):
        s.gate.approve(res["task_id"])


def test_pending_then_reject_deprecates_tool(make_stack):
    s = make_stack(make_llm([ADD]), auto_approve=False)
    res = s.pipeline.run("x")["results"][0]
    s.gate.reject(res["task_id"], "not wanted")
    assert s.registry.list_loaded() == []
    assert s.librarian.get_tool(res["task_id"])["status"] == "deprecated"
    # deprecated tools are never offered for reuse
    assert s.pipeline.run("x")["reused"] == 0


def test_pending_approval_does_not_load_on_reuse(make_stack):
    s = make_stack(make_llm([ADD]), auto_approve=False)
    s.pipeline.run("x")
    again = s.pipeline.run("x")
    assert again["reused"] == 1
    assert again["results"][0]["approval"] == "pending" and again["results"][0]["loaded"] is False
    assert len(s.gate.list_pending()) == 1


def test_reserved_tool_name_is_not_loaded(make_stack):
    health = task_spec(
        "Health check",
        "from health_check import health_check\n\n\ndef test_h():\n"
        "    assert health_check() == 'ok'\n    assert isinstance(health_check(), str)\n",
        'def health_check() -> str:\n    """Health check."""\n    return "ok"\n',
    )
    s = make_stack(make_llm([health]))
    res = s.pipeline.run("x")["results"][0]
    assert res["status"] == "verified" and res["loaded"] is False
    assert "reserved" in res["load_error"]
    assert s.registry.list_loaded() == []


def test_gate_persists_pending_state(tmp_path):
    from src.approval import ToolProposal

    state = tmp_path / "pending.json"
    gate = ApprovalGate(auto_approve=False, state_path=state)
    gate.request(ToolProposal(tool_id="t1", name="f", path="f.py"))
    assert [p["tool_id"] for p in ApprovalGate(state_path=state).list_pending()] == ["t1"]


# -- helper ----------------------------------------------------------------

def test_bind_test_imports_rewrites_guessed_module(tmp_path):
    p = tmp_path / "test_x.py"
    p.write_text(
        "import pytest\nfrom tools.add_two_integers import add_two_integers\n"
        "from pathlib import Path\n\n\ndef test_a():\n    assert add_two_integers(1, 1) == 2\n",
        encoding="utf-8",
    )
    assert bind_test_imports(str(p), "add_two_integers_task_1_abcd1234", "add_two_integers") is True
    text = p.read_text(encoding="utf-8")
    assert "from add_two_integers_task_1_abcd1234 import add_two_integers" in text
    assert "from pathlib import Path" in text
    assert bind_test_imports(str(p), "add_two_integers_task_1_abcd1234", "add_two_integers") is False


def test_bind_test_imports_ignores_unrelated_and_invalid(tmp_path):
    p = tmp_path / "test_y.py"
    p.write_text("from os.path import join\n", encoding="utf-8")
    assert bind_test_imports(str(p), "mod", "add_two_integers") is False
    p.write_text("def (:\n", encoding="utf-8")
    assert bind_test_imports(str(p), "mod", "add_two_integers") is False
