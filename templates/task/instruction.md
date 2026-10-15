Implement a control policy for the `TemplateReach` environment and leave it runnable at
/home/user/submission/run.sh

TemplateReach is a 2-D point mass with damping. Each step you receive the observation
`[x, y, vx, vy, gx, gy]` — position, velocity, and goal — and return an acceleration
`[ax, ay]`, each component clipped to [-1, 1]. An episode runs 200 steps, and the goal and
the starting state are drawn fresh at scoring time.

You are measured on the **mean distance to the goal, averaged over every step of every
episode** — so reach the goal fast and stay on it. Lower is better. Your score compares
that distance to a reference implementation's real measured value on hidden seeds. Full
credit is reached at or above the reference's level (the reward saturates there); the
reference value itself is not disclosed.

## What to build

Write /home/user/submission/run.sh so that running it starts a long-lived process that
speaks one JSON object per line on stdin/stdout. The grader launches it as `bash run.sh`
with the working directory set to `/home/user/submission`, under your own user account.

```
                                  <- READY                  (when you are loaded)
{"type": "reset"}                 -> {"ok": true}
{"type": "act", "t": 0, "obs": [x, y, vx, vy, gx, gy]}
                                  -> {"action": [ax, ay]}
{"type": "close"}                 -> {"ok": true}, then exit
```

Keep stdout clean — it is the wire. Send diagnostics to stderr.

Deadlines: print `READY` within 120 s of launch (load whatever you need first, then print
it), and answer every request within 30 s. A missed deadline, a reply that is not one JSON
object with an `action` of two numbers, or a crash aborts the episode, which is then charged
as if you had not moved at all. After an aborted episode the grader stops your process and
launches `bash run.sh` again for the next one (it must print `READY` again); after three
such restarts every remaining episode aborts. So `run.sh` has to work when started a second
time: no fixed ports, PID files or locks that the first run leaves behind.

`t` is the step index within the episode, starting at 0 after each `reset`. A step may be
queried more than once with the same `t`; answer each request from the observation it
carries. Repeated queries do not advance the episode — do not count requests as steps; use
`t`. The grader checks that your action actually depends on the observation it was given:
a policy that ignores its input, replays a recorded sequence, or returns a constant scores
zero regardless of its metric.

## Environment

`python3` is `/opt/venv/bin/python3` (Python 3.12) with `numpy` installed; there is no
network access. Your development copy of the environment is `/home/user/template_env.py`
(`TemplateReach`, with `reset(seed)` and `step(action)`).

## Checking your work

`python3 /home/user/practice_grader/grade.py` runs your solver against the same
environment on visible practice seeds, over the same wire — same request shape, same
deadlines, and a few repeated-`t` queries — and prints the raw metric it observes. It uses
different seeds than the real grading run and reports no score — it tells you your policy
works, not how well you did.
