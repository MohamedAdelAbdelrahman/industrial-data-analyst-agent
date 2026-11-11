"""run_python sandbox: functionality and isolation. Skipped when Docker or the image is unavailable."""
import pytest

from ida.mcp_server.datasets import DatasetStore
from ida.mcp_server.tools import RunPythonTool

docker = pytest.importorskip("docker")


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    from ida.mcp_server.sandbox import PythonSandbox
    try:
        box = PythonSandbox(tmp_path_factory.mktemp("artifacts"))
        box.client.ping()
    except docker.errors.DockerException:
        pytest.skip("Docker is not running")
    if not box.image_exists():
        pytest.skip("sandbox image missing: python -m ida.mcp_server.sandbox --build")
    return box


@pytest.fixture
def tool(sandbox, tmp_path):
    store = DatasetStore(tmp_path / "datasets")
    return RunPythonTool(sandbox, store), store


# ---------- functionality ----------

def test_stdout_and_exit_code(tool):
    run, _ = tool
    out = run("print(6 * 7)")
    assert out["error"] is None and out["stdout"].strip() == "42" and out["exit_code"] == 0


def test_exception_is_reported(tool):
    run, _ = tool
    out = run("raise ValueError('boom')")
    assert out["exit_code"] == 1 and "ValueError: boom" in out["stderr"] and out["error"]


def test_dataset_is_mounted_read_only(tool):
    run, store = tool
    ds = store.save("SELECT ...", ["machine", "alarms"], [("Pump-01", 3), ("Pump-02", 5)])
    out = run(f"import pandas as pd\ndf = pd.read_csv('/data/{ds}.csv')\nprint(df.alarms.sum())\n"
              f"open('/data/{ds}.csv', 'a').write('x')", [ds])
    assert out["stdout"].strip() == "8"
    assert "Read-only file system" in out["stderr"]


def test_unknown_dataset_is_rejected(tool):
    run, _ = tool
    assert "Unknown dataset_id" in run("print(1)", ["ds_0000000000"])["error"]
    assert "Invalid dataset id" in run("print(1)", ["../../etc/passwd"])["error"]


def test_plot_is_returned_as_file(tool):
    run, _ = tool
    out = run("import matplotlib.pyplot as plt\nplt.plot([1, 3, 2])\nplt.savefig('trend.png')")
    assert out["error"] is None
    assert [i["name"] for i in out["images"]] == ["trend.png"]
    with open(out["images"][0]["path"], "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_output_is_truncated(tool):
    run, _ = tool
    out = run("print('x' * 50_000)")
    assert len(out["stdout"]) < 11_000 and "truncated" in out["stdout"]


# ---------- isolation ----------

def test_no_network(tool):
    run, _ = tool
    out = run("import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)")
    assert out["exit_code"] != 0 and "OSError" in out["stderr"]


def test_runs_as_non_root_without_capabilities(tool):
    run, _ = tool
    out = run("import os\nprint(os.getuid())\n"
              "print([l for l in open('/proc/self/status') if l.startswith('CapEff')][0].split()[1])")
    uid, cap_eff = out["stdout"].split()
    assert uid == "1000" and int(cap_eff, 16) == 0


def test_root_filesystem_is_read_only(tool):
    run, _ = tool
    out = run("open('/tmp/ok.txt', 'w').write('tmp is writable')\nprint('tmp ok')\n"
              "open('/usr/local/lib/evil.py', 'w')")
    assert "tmp ok" in out["stdout"]
    assert "Read-only file system" in out["stderr"] or "Permission denied" in out["stderr"]


def test_timeout_kills_infinite_loop(tool):
    run, _ = tool
    out = run("while True:\n    pass", timeout_s=3)
    assert out["timed_out"] and "Timed out" in out["error"]
    assert out["duration_s"] < 15


def test_memory_limit(tool):
    run, _ = tool
    out = run("x = bytearray(1500 * 1024 * 1024)\nprint('allocated')")
    assert "allocated" not in out["stdout"]
    assert out["oom_killed"] or "MemoryError" in out["stderr"]


def test_process_limit(tool):
    run, _ = tool
    out = run("import os, time\n"
              "n = 0\n"
              "try:\n"
              "    for _ in range(200):\n"
              "        if os.fork() == 0:\n"
              "            time.sleep(5); os._exit(0)\n"
              "        n += 1\n"
              "except OSError as e:\n"
              "    print('blocked after', n, type(e).__name__)\n", timeout_s=20)
    assert "blocked after" in out["stdout"]
    assert int(out["stdout"].split()[2]) < 64


# ---------- mounts (no Docker daemon needed) ----------

def _offline_sandbox(monkeypatch, tmp_path, **kwargs):
    from ida.mcp_server.sandbox import PythonSandbox
    monkeypatch.setattr(docker, "from_env", lambda: None)
    return PythonSandbox(tmp_path / "artifacts", **kwargs)


def test_host_mode_bind_mounts_the_run_folder(monkeypatch, tmp_path):
    box = _offline_sandbox(monkeypatch, tmp_path)
    m = box._mount(tmp_path / "run1", "out", "/out", read_only=False)
    assert (m["Type"], m["Source"], m["Target"], m["ReadOnly"]) == ("bind", str(tmp_path / "run1" / "out"), "/out", False)


def test_volume_mode_mounts_only_the_runs_subpath(monkeypatch, tmp_path):
    """In docker compose the daemon can't see the agent container's paths: mount a subpath of the volume."""
    box = _offline_sandbox(monkeypatch, tmp_path, work_dir=tmp_path / "work", volume="ida-sandbox-work")
    m = box._mount(tmp_path / "work" / "ida_run_abc", "code", "/code", read_only=True)
    assert (m["Type"], m["Source"], m["Target"], m["ReadOnly"]) == ("volume", "ida-sandbox-work", "/code", True)
    assert m["VolumeOptions"]["Subpath"] == "ida_run_abc/code"


def test_volume_without_work_dir_is_rejected(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        _offline_sandbox(monkeypatch, tmp_path, volume="ida-sandbox-work")
