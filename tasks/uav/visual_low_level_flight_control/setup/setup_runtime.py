"""Trusted setup; only the client, not the world implementation, is made public."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    stage = Path(__file__).resolve().parent
    private = Path("/var/lib/visual_robotics")
    if private.exists():
        raise RuntimeError("refusing to reset an existing episode")
    private.mkdir(mode=0o700)
    shutil.copytree(stage/"runtime", private/"runtime")
    public = Path("/opt/visual_robotics/client")
    public.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage/"payload", public, dirs_exist_ok=True)
    # Public clients are immutable; the agent can copy them but cannot change MCP execution.
    for path in public.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    public.chmod(0o755)
    shutil.copytree(public, Path("/home/user/robot"), dirs_exist_ok=True)
    kind = json.loads((stage/"kind.json").read_text())["kind"]
    with (private/"service.log").open("wb") as log:
        process = subprocess.Popen(
            ["/opt/venv/bin/python3", str(private/"runtime"/"service.py"), "--kind", kind],
            stdout=log, stderr=log, start_new_session=True,
            cwd=private, env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
        )
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError((private/"service.log").read_text())
        if Path("/run/visual_robotics/world.sock").exists():
            return
        time.sleep(.1)
    process.terminate()
    raise RuntimeError("world service did not become ready")


if __name__ == "__main__":
    main()
