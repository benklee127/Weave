"""Tests for WardenAgent."""

import textwrap
from pathlib import Path

import pytest

from src.agents.warden import WardenAgent
from src.core.schemas import TaskNode, TestExecutionResult
from src.sandbox.base import SandboxRunner
from src.sandbox.subprocess_runner import SubprocessRunner


def make_task(**kw) -> TaskNode:
    base = dict(id="task_1", description="add numbers")
    base.update(kw)
    return TaskNode(**base)


class RecordingRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run_test(self, test_file_path, task_id, implementation_path=None):
        self.calls.append((test_file_path, task_id, implementation_path))
        return self.result


def res(success=False, failed=1, passed=0, stderr=""):
    return TestExecutionResult(task_id="task_1", success=success, exit_code=0 if success else 1,
                               tests_failed=failed, tests_passed=passed, stderr=stderr)


def _write(path: Path, body: str) -> str:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def test_default_runner_from_settings():
    w = WardenAgent()
    assert isinstance(w.runner, SandboxRunner)
    assert isinstance(WardenAgent(timeout=5).runner, SubprocessRunner)


def test_execute_tests_delegates():
    runner = RecordingRunner(res(True, 0, 2))
    w = WardenAgent(runner=runner)
    out = w.execute_tests(make_task(test_suite_path="t.py", implementation_path="i.py"))
    assert out.success
    assert runner.calls == [("t.py", "task_1", "i.py")]


def test_execute_tests_requires_suite():
    with pytest.raises(ValueError):
        WardenAgent(runner=RecordingRunner(res())).execute_tests(make_task())


def test_validate_test_failure():
    w = WardenAgent(runner=RecordingRunner(res()))
    assert w.validate_test_failure(res(False, 2))
    assert not w.validate_test_failure(res(True, 0, 3))
    assert not w.validate_test_failure(res(False, 0))
    assert not w.validate_test_failure(res(False, 1, stderr="[sandbox] INFRA ERROR: x"))


def test_red_check_real_good_tests(tmp_path):
    test = _write(tmp_path / "test_calc.py", """
        from calc_mod import add
        def test_add():
            assert add(1, 2) == 3
    """)
    w = WardenAgent(runner=SubprocessRunner(timeout=60))
    assert w.run_red_check(make_task(test_suite_path=test)) is True


def test_red_check_with_implementation_path_uses_its_stem(tmp_path):
    (tmp_path / "impl").mkdir()
    impl = _write(tmp_path / "impl" / "calc_mod.py", "def add(a, b):\n    return a + b\n")
    test = _write(tmp_path / "test_calc.py", """
        from calc_mod import add
        def test_add():
            assert add(1, 2) == 3
    """)
    w = WardenAgent(runner=SubprocessRunner(timeout=60))
    task = make_task(test_suite_path=test, implementation_path=impl)
    assert w.run_red_check(task) is True
    assert w.execute_tests(task).success


def test_red_check_detects_vacuous_tests(tmp_path):
    test = _write(tmp_path / "test_vac.py", """
        from vac_mod import thing
        def test_nothing():
            assert True
    """)
    w = WardenAgent(runner=SubprocessRunner(timeout=60))
    assert w.run_red_check(make_task(test_suite_path=test)) is False


def test_red_check_no_names_or_no_suite(tmp_path):
    test = _write(tmp_path / "test_plain.py", "def test_a():\n    assert False\n")
    w = WardenAgent(runner=RecordingRunner(res()))
    assert w.run_red_check(make_task(test_suite_path=test)) is False
    assert w.run_red_check(make_task()) is False
    assert w.run_red_check(make_task(test_suite_path=str(tmp_path / "gone.py"))) is False


def test_red_check_passes_stub_to_runner(tmp_path):
    test = _write(tmp_path / "test_x.py", "from foo_mod import a, b\ndef test_a():\n    a()\n")
    seen = {}

    class Spy:
        def run_test(self, test_file_path, task_id, implementation_path=None):
            seen["path"] = Path(implementation_path)
            seen["src"] = seen["path"].read_text(encoding="utf-8")
            return res()

    assert WardenAgent(runner=Spy()).run_red_check(make_task(test_suite_path=test))
    assert seen["path"].name == "foo_mod.py"
    assert "def a(" in seen["src"] and "def b(" in seen["src"]
    assert not seen["path"].exists(), "temp stub should be cleaned up"
