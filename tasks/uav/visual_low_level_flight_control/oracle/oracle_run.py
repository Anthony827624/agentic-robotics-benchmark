"""Unprivileged image-only oracle. This proves feasibility, not frontier difficulty."""
import json
import base64
from pathlib import Path
import shutil
import sys

from client import Client
from policy import solve


class RecordedClient(Client):
    def __init__(self, out):
        super().__init__()
        self.out, self.events = out, []

    def request(self, method, **params):
        result = super().request(method, **params)
        self.events.append({"method": method, "params": params,
                            "observation": {k: v for k, v in result.items() if k != "images"}})
        for i, image in enumerate(result["images"]):
            (self.out/f"final-camera-{i}.png").write_bytes(base64.b64decode(image))
            if len(self.events) == 1:
                (self.out/f"initial-camera-{i}.png").write_bytes(base64.b64decode(image))
        (self.out/"example_interaction.json").write_text(json.dumps(self.events, indent=2))
        return result


def main():
    here = Path(__file__).resolve().parent
    kind = json.loads((here/"kind.json").read_text())["kind"]
    out = Path("/home/user/submission")
    shutil.copy2(here/"policy.py", out/"policy.py")
    (out/"run.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\n"
        "exec /opt/venv/bin/python3 policy.py\n"
    )
    (out/"run.sh").chmod(0o755)
    result = solve(RecordedClient(out), kind, artifacts=out)
    (out/"reference_result.json").write_text(json.dumps({
        "kind": kind, "done": result["done"],
        "steps_remaining": result["steps_remaining"], "scripted_reference": True,
    }, indent=2))
    print(json.dumps({"kind": kind, "steps_remaining": result["steps_remaining"]}), flush=True)


if __name__ == "__main__":
    main()
