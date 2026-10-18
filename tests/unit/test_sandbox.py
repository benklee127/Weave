"""Tests for sandbox runners and the factory (no network, no docker needed)."""

import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.sandbox import docker_runner as dr
from src.sandbox.base import SandboxRunner, build_result, is_infra_error, truncate_output
from src.sandbox.docker_runner import DockerRunner
from src.sandbox.factory import create_runner
from src.sandbox.subprocess_runner import SubprocessRunner, build_env


def write(path: Path, body: str) -> str:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------- subprocess
def test_pass_with_implementation(tmp_path):
    impl = write(tmp_path / "adder_mod.py", "def add(a, b):\n    return a + b\n")
    test = write(tmp_path / "test_adder.py", """
        from adder_mod import add
        def test_add():
            assert add(1, 2) == 3
        def test_add2():
            assert add(0, 0) == 0
    """)
    r = SubprocessRunner(timeout=60).run_test(test, "t1", impl)
    assert r.success and r.exit_code == 0
    assert r.tests_passed == 2 and r.tests_failed == 0
    assert r.task_id == "t1"


def test_failure_counts(tmp_path):
    test = write(tmp_path / "test_f.py", """
        def test_a():
            assert False
        def test_b():
            assert True
    """)
    r = SubprocessRunner(timeout=60).run_test(test, "t")
    assert not r.success and r.tests_failed == 1 and r.tests_passed == 1


def test_collection_error_reported_as_infra(tmp_path):
    test = write(tmp_path / "test_imp.py", "import does_not_exist_xyz\ndef test_a():\n    pass\n")
    r = SubprocessRunner(timeout=60).run_test(test, "t")
    assert not r.success and r.exit_code == 2
    assert is_infra_error(r)
    assert "INFRA ERROR" in r.stderr


def test_no_tests_collected_is_infra(tmp_path):
    test = write(tmp_path / "test_empty.py", "x = 1\n")
    r = SubprocessRunner(timeout=60).run_test(test, "t")
    assert r.exit_code == 5 and not r.success and is_infra_error(r)


def test_missing_files(tmp_path):
    r = SubprocessRunner().run_test(str(tmp_path / "nope.py"), "t")
    assert not r.success and "not found" in r.stderr
    test = write(tmp_path / "test_x.py", "def test_a():\n    pass\n")
    r = SubprocessRunner().run_test(test, "t", str(tmp_path / "missing.py"))
    assert not r.success and "not found" in r.stderr


def test_timeout_kills_process_tree(tmp_path):
    marker = tmp_path / "child_alive.txt"
    test = write(tmp_path / "test_slow.py", f"""
        import subprocess, sys, time
        def test_slow():
            subprocess.Popen([sys.executable, "-c",
                "import time; time.sleep(5); open('{marker.as_posix()}', 'w').write('x')"])
            time.sleep(60)
    """)
    r = SubprocessRunner(timeout=3).run_test(test, "t")
    assert not r.success and "timed out" in r.stderr
    assert r.execution_time < 30
    time.sleep(4)
    assert not marker.exists(), "grandchild process survived the timeout"


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_TOKEN", "tok")
    env = build_env(["/x"])
    assert "ANTHROPIC_API_KEY" not in env and "MY_TOKEN" not in env
    assert env["PYTHONPATH"] == "/x"
    test = write(tmp_path / "test_env.py", """
        import os
        def test_no_secret():
            assert "ANTHROPIC_API_KEY" not in os.environ
            assert "MY_TOKEN" not in os.environ
    """)
    assert SubprocessRunner(timeout=60).run_test(test, "t").success


def test_output_is_capped(tmp_path):
    test = write(tmp_path / "test_big.py", """
        def test_big():
            print("x" * 2_000_000)
            assert False
    """)
    r = SubprocessRunner(timeout=60, max_output_chars=1000).run_test(test, "t")
    assert len(r.stdout) < 20000 and "truncated" in r.stdout


def test_truncate_output_helper():
    assert truncate_output("abc", 10) == "abc"
    out = truncate_output("a" * 100, 10)
    assert "truncated" in out and out.startswith("aaaaa") and out.endswith("aaaaa")


def test_build_result_infra_note():
    r = build_result("t", 4, "", "boom", 0.1)
    assert is_infra_error(r) and "boom" in r.stderr


def test_runners_satisfy_protocol():
    assert isinstance(SubprocessRunner(), SandboxRunner)
    assert isinstance(DockerRunner(client=MagicMock()), SandboxRunner)


# -------------------------------------------------------------------- factory
def test_factory_selects_runner():
    s = Settings(_env_file=None, sandbox_type="subprocess", sandbox_timeout=11)
    r = create_runner(s)
    assert isinstance(r, SubprocessRunner) and r.timeout == 11
    s = Settings(_env_file=None, sandbox_type="docker", sandbox_memory_limit="256m",
                 sandbox_docker_image="img:1")
    r = create_runner(s)
    assert isinstance(r, DockerRunner)
    assert r.memory_limit == "256m" and r.image == "img:1"


# --------------------------------------------------------------------- docker
def _mock_client(status=0, out=b"1 passed in 0.1s", err=b""):
    client = MagicMock()
    container = MagicMock()
    container.wait.return_value = {"StatusCode": status}

    def logs(stdout=True, stderr=True):
        return out if stdout else err

    container.logs.side_effect = logs
    client.containers.run.return_value = container
    return client, container


def test_docker_kwargs_construction(tmp_path):
    test = write(tmp_path / "test_d.py", "def test_a():\n    pass\n")
    impl = write(tmp_path / "mod.py", "x = 1\n")
    client, container = _mock_client()
    runner = DockerRunner(image="weave:test", timeout=30, memory_limit="128m",
                          cpu_quota=25000, client=client)
    r = runner.run_test(test, "tid", impl)

    assert r.success and r.tests_passed == 1
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["image"] == "weave:test"
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["mem_limit"] == "128m" and kwargs["memswap_limit"] == "128m"
    assert kwargs["cpu_quota"] == 25000 and kwargs["cpu_period"] == dr.CPU_PERIOD
    assert kwargs["pids_limit"] > 0
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"]
    assert kwargs["user"] not in ("", "0", "root", "0:0")
    assert kwargs["detach"] is True
    vols = kwargs["volumes"]
    assert len(vols) == 2 and all(v["mode"] == "ro" for v in vols.values())
    binds = {v["bind"] for v in vols.values()}
    assert binds == {"/work/tests/test_d.py", "/work/impl/mod.py"}
    assert kwargs["command"][:3] == ["python", "-m", "pytest"]
    assert "/work/tests/test_d.py" in kwargs["command"]
    assert kwargs["environment"]["PYTHONPATH"] == "/work/impl"
    assert not any("KEY" in k for k in kwargs["environment"])
    container.remove.assert_called_once_with(force=True)
    client.images.pull.assert_not_called()


def test_docker_failure_and_infra(tmp_path):
    test = write(tmp_path / "test_d.py", "def test_a():\n    pass\n")
    client, _ = _mock_client(status=1, out=b"1 failed, 2 passed in 0.1s")
    r = DockerRunner(client=client).run_test(test, "t")
    assert not r.success and r.tests_failed == 1 and r.tests_passed == 2

    client, _ = _mock_client(status=2, out=b"", err=b"ImportError")
    r = DockerRunner(client=client).run_test(test, "t")
    assert is_infra_error(r)


def test_docker_timeout_kills_container(tmp_path):
    test = write(tmp_path / "test_d.py", "def test_a():\n    pass\n")
    client, container = _mock_client()
    container.wait.side_effect = TimeoutError("read timed out")
    r = DockerRunner(timeout=1, client=client).run_test(test, "t")
    assert not r.success and "timed out" in r.stderr
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True)


def test_docker_run_error_has_hint(tmp_path):
    test = write(tmp_path / "test_d.py", "def test_a():\n    pass\n")

    class ImageNotFound(Exception):
        pass

    client = MagicMock()
    client.containers.run.side_effect = ImageNotFound("no such image")
    r = DockerRunner(image="nope:1", client=client).run_test(test, "t")
    assert not r.success and "not present locally" in r.stderr
    client.images.pull.assert_not_called()


def test_docker_unavailable_gives_clear_error(tmp_path, monkeypatch):
    test = write(tmp_path / "test_d.py", "def test_a():\n    pass\n")
    runner = DockerRunner()

    def boom():
        raise dr.DockerUnavailableError("Docker daemon unreachable: nope")

    monkeypatch.setattr(runner, "_get_client", boom)
    r = runner.run_test(test, "t")
    assert not r.success and "unreachable" in r.stderr and is_infra_error(r)
    assert runner.is_available() is False


@pytest.mark.docker
def test_docker_live_run(tmp_path):
    """Runs a real container only if the daemon and the image already exist."""
    runner = DockerRunner(timeout=120)
    if not runner.is_available():
        pytest.skip("Docker daemon not reachable")
    client = runner._get_client()
    try:
        client.images.get(runner.image)
    except Exception:
        pytest.skip(f"Image {runner.image} not present locally (tests never pull)")
    test = write(tmp_path / "test_live.py", "def test_a():\n    assert 1 + 1 == 2\n")
    r = runner.run_test(test, "live")
    if is_infra_error(r) or r.exit_code in (-1, 127):
        pytest.skip(f"Image cannot run pytest: {r.stderr[:200]}")
    assert r.success
