"""Single-threaded, root-owned world service with an append-only private event log."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socketserver

from world import World


class Runtime:
    def __init__(self, kind, directory, seed=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.seed = secrets.randbits(64) if seed is None else seed
        self.world = World(kind, self.seed)
        self.count = 0
        self.events = []
        (self.directory/"frames").mkdir(exist_ok=True, mode=0o700)
        self.flush()

    def flush(self):
        state = {"seed": self.seed, "kind": self.world.kind, "events": self.events,
                 "metrics": self.world.metrics(), "phase": self.world.phase,
                 "requests": self.count}
        temp = self.directory/"state.tmp"
        temp.write_text(json.dumps(state))
        temp.replace(self.directory/"state.json")

    def call(self, req):
        self.count += 1
        if self.count > 2048:
            self.flush()
            raise ValueError("request budget exhausted")
        if not isinstance(req, dict) or set(req) != {"method", "params"}:
            self.flush()
            raise ValueError("invalid request envelope")
        method, params = req["method"], req["params"]
        event = {"method": method, "params": params}
        self.events.append(event)
        try:
            result = self.dispatch(method, params)
            event["frame_id"] = result["frame_id"]
            return result
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            event["error"] = str(exc)
            raise
        finally:
            self.flush()

    def dispatch(self, method, params):
        allowed = {"observe": set(), "act": {"action"}, "advance": set(),
                   "report": {"frame_id", "point"}}
        if not isinstance(method, str) or method not in allowed or not isinstance(params, dict) or set(params) != allowed[method]:
            raise ValueError("operation/arguments not allowed; reset is never available")
        result = getattr(self.world, method)(**params)
        for i, encoded in enumerate(result["images"]):
            (self.directory/"frames"/f'{result["frame_id"]:04d}-{i}.png').write_bytes(
                base64.b64decode(encoded))
        return result


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            line = self.rfile.readline(8193)
            if len(line) > 8192:
                raise ValueError("request too large")
            result = self.server.runtime.call(json.loads(line))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            result = {"error": str(exc)}
        self.wfile.write((json.dumps(result)+"\n").encode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--directory", default="/var/lib/visual_robotics")
    parser.add_argument("--socket", default="/run/visual_robotics/world.sock")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    runtime = Runtime(args.kind, args.directory, args.seed)
    path = Path(args.socket)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError("refuse to overwrite a live or pre-existing socket")
    (Path(args.directory)/"pid").write_text(str(os.getpid()))
    with socketserver.UnixStreamServer(str(path), Handler) as server:
        os.chmod(path, 0o666)
        server.runtime = runtime
        server.serve_forever()


if __name__ == "__main__":
    main()
