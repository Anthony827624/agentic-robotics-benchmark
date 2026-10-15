"""Oracle solver — loads the trained PPO policy and serves the closed-loop
obs->action protocol on stdin/stdout. Self-contained (hand-rolled wire loop, no
shared kit): at grading time it only LOADS best_model.zip — it does not train.

Wire (one JSON object per line): print READY once loaded, then
  {"type": "reset"}                    -> {"ok": true}
  {"type": "act", "obs": {...}, ...}   -> {"action": [...]}
  {"type": "close"}                    -> {"ok": true} and exit.
Diagnostics go to stderr; stdout carries ONLY the protocol.
"""

import json
import os
import sys

import numpy as np
from stable_baselines3 import PPO

HERE = os.path.dirname(os.path.abspath(__file__))
_model = PPO.load(os.path.join(HERE, "best_model"))


def act(obs):
    vec = np.asarray(obs["obs"], dtype=np.float32)
    action, _ = _model.predict(vec, deterministic=True)
    return np.asarray(action, dtype=np.float32).ravel().tolist()


def main():
    print("READY", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            print(json.dumps({"error": "bad json"}), flush=True)
            continue
        kind = req.get("type")
        if kind == "reset":
            print(json.dumps({"ok": True}), flush=True)   # feed-forward: no state
        elif kind == "act":
            try:
                print(json.dumps({"action": act(req["obs"])}), flush=True)
            except Exception as e:  # noqa: BLE001 — report on the wire, keep serving
                print(json.dumps({"error": str(e)[:200]}), flush=True)
        elif kind == "close":
            print(json.dumps({"ok": True}), flush=True)
            return
        else:
            print(json.dumps({"error": "unknown request"}), flush=True)


if __name__ == "__main__":
    main()
