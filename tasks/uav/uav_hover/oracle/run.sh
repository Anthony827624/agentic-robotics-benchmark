#!/usr/bin/env bash
# Oracle — the task's own known-good solution, run in place of the agent by
# `ale validate` AS THE AGENT USER with the agent's limits. A minimal port of the
# simulator repo's own PPO recipe for HoverAviary (learn.py: PPO on kin/one_d_rpm),
# which is the reference implementation the anchor was measured from: TRAIN here, then leave a self-contained /home/user/submission/
# (checkpoint + solver + run.sh) for the verify stage to drive closed-loop.
set -euo pipefail
export PATH="/opt/venv/bin:$PATH"

SUB="${ALE_HOME:-/home/user}/submission"
mkdir -p "$SUB"
TIMESTEPS="${GPD_TIMESTEPS:-300000}"

echo "[oracle] training PPO HoverAviary (timesteps=$TIMESTEPS) ..." >&2
python3 - "$SUB" "$TIMESTEPS" <<'PY'
import sys
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import (EvalCallback,
                                                StopTrainingOnRewardThreshold)
from gym_pybullet_drones.envs.HoverAviary import HoverAviary
from gym_pybullet_drones.utils.enums import ActionType, ObservationType

sub, timesteps = sys.argv[1], int(sys.argv[2])
kwargs = dict(obs=ObservationType("kin"), act=ActionType("one_d_rpm"))
env, eval_env = HoverAviary(**kwargs), HoverAviary(**kwargs)
model = PPO("MlpPolicy", env, seed=0, verbose=0)
# The repo's own solve criterion: stop when the eval reward crosses 474.
stop_cb = StopTrainingOnRewardThreshold(reward_threshold=474.0, verbose=1)
eval_cb = EvalCallback(eval_env, callback_on_new_best=stop_cb, eval_freq=2000,
                       best_model_save_path=sub, verbose=1)
model.learn(total_timesteps=timesteps, callback=eval_cb)
import os
best = os.path.join(sub, "best_model.zip")
if not os.path.isfile(best):
    model.save(os.path.join(sub, "best_model"))
print("[oracle] training done ->", sub)
PY

# The solver + run.sh (copied into submission/, self-contained — no kit needed).
cp "$(dirname "$0")/solver.py" "$SUB/solver.py"
cat > "$SUB/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
export HOME=/home/user
export PATH="/opt/venv/bin:$PATH"
cd "$(dirname "$0")"
exec python3 solver.py
SH
chmod +x "$SUB/run.sh"
echo "[oracle] submission ready at $SUB" >&2
