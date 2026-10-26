"""run_python: execute untrusted analysis code in a locked-down Docker container.

Every run gets a fresh container:
  --network none, read-only root FS, tmpfs /tmp, 512 MB RAM (no swap), 1 CPU, 64 PIDs,
  non-root uid 1000, all capabilities dropped, no-new-privileges, wall-clock timeout.
Mounts: code (ro) at /code, requested datasets (ro) at /data/<id>.csv, and /out (rw, cwd)
from which *.png files are collected.

Build the image:
    python -m ida.mcp_server.sandbox --build
"""
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import docker
import requests

from ida.config import ROOT

IMAGE = "ida-sandbox:latest"
MAX_OUTPUT_CHARS = 10_000
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 2_000_000


@dataclass
class SandboxLimits:
    memory: str = "512m"
    cpus: float = 1.0
    pids: int = 64
    tmpfs_size: str = "64m"


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated {len(text) - MAX_OUTPUT_CHARS} chars]"


class PythonSandbox:
    def __init__(self, artifacts_dir: Path, limits: SandboxLimits | None = None):
        self.artifacts_dir = artifacts_dir
        self.limits = limits or SandboxLimits()
        self.client = docker.from_env()

    def image_exists(self) -> bool:
        try:
            self.client.images.get(IMAGE)
            return True
        except docker.errors.ImageNotFound:
            return False

    def build_image(self) -> None:
        self.client.images.build(path=str(ROOT / "sandbox"), tag=IMAGE, rm=True)

    def run(self, code: str, datasets: dict[str, Path] | None = None, timeout_s: float = 20) -> dict:
        """datasets maps dataset_id -> host CSV path. Returns stdout/stderr/exit_code/images."""
        run_id = uuid.uuid4().hex[:12]
        work = Path(tempfile.mkdtemp(prefix=f"ida_run_{run_id}_"))
        try:
            (work / "code").mkdir()
            (work / "out").mkdir()
            (work / "code" / "main.py").write_text(code, encoding="utf-8")
            volumes = {str(work / "code"): {"bind": "/code", "mode": "ro"},
                       str(work / "out"): {"bind": "/out", "mode": "rw"}}
            for ds_id, path in (datasets or {}).items():
                volumes[str(path.resolve())] = {"bind": f"/data/{ds_id}.csv", "mode": "ro"}

            container = self.client.containers.run(
                IMAGE, detach=True, volumes=volumes,
                network_mode="none", read_only=True,
                tmpfs={"/tmp": f"size={self.limits.tmpfs_size},mode=1777"},
                mem_limit=self.limits.memory, memswap_limit=self.limits.memory,
                nano_cpus=int(self.limits.cpus * 1e9), pids_limit=self.limits.pids,
                user="1000:1000", cap_drop=["ALL"], security_opt=["no-new-privileges"],
            )
            start = time.monotonic()
            timed_out = False
            try:
                exit_code = container.wait(timeout=timeout_s)["StatusCode"]
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                timed_out = True
                container.kill()
                exit_code = -1
            container.reload()
            oom = container.attrs["State"].get("OOMKilled", False)
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
            container.remove(force=True)

            return {
                "run_id": run_id,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "oom_killed": oom,
                "duration_s": round(time.monotonic() - start, 2),
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
                "images": self._collect_images(work / "out", run_id),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _collect_images(self, out_dir: Path, run_id: str) -> list[dict]:
        """Copy PNGs to the artifacts dir; return paths (not base64) to keep LLM context small."""
        images = []
        for png in sorted(out_dir.glob("*.png"))[:MAX_IMAGES]:
            if png.stat().st_size > MAX_IMAGE_BYTES:
                continue
            dest = self.artifacts_dir / run_id / png.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(png, dest)
            images.append({"name": png.name, "path": str(dest)})
        return images


if __name__ == "__main__":
    import argparse

    from ida.config import get_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="(re)build the sandbox image")
    args = ap.parse_args()
    box = PythonSandbox(get_settings().artifacts_dir)
    if args.build or not box.image_exists():
        print(f"building {IMAGE} ...")
        box.build_image()
    print(box.run("import sys, pandas, matplotlib; print('ok', sys.version.split()[0], pandas.__version__)"))
