# Reviewer Guide

> **Maintainer changes, 2026-10-29 (v8).** The task as reviewed (v7) had four problems: the probe perturbation (a
> view from 0.5-1.4 m away) zeroed honest controllers that fly short stretches from memory and contaminated stateful
> ones; the scene randomization was narrower than the gate tolerance, so a recorded flight replayed open-loop passed
> 83-99 % of fresh scenes; the verify stage had an unstated 900 s cap; four binary scenes gave a coarse score. v8:
> gates 0.7 m (crossing window 0.26 m); the whole course turned by a random multiple of 90 degrees plus up to
> 0.18 rad, gates moved by up to 0.25 m and turned by up to 0.2 rad, the start moved by up to 0.25 m and its heading
> drawn within 90 degrees (layouts too close to the cabinet are redrawn); eight scenes; probes from a viewpoint
> 3-6 cm and 1-3 degrees away, at least a quarter of them must change the action; a stated 1500 s budget of controller
> reply time (`timeouts.verify` 5400 s: up to 2,640 onboard frames at about 0.55 s each on the task's two CPUs, plus
> the full budget); the oracle searches for the first gate and uses the new marker size. Measured:
> a recorded oracle flight replayed on 128 fresh scenes completes 0 (0 of 96 with the same quarter turn); the oracle
> completed 24/24 scenes in three `ale validate` runs and 63/64 offline. The v7 notes below are historical.
>
> **Maintainer changes, 2026-10-30/31 (the onboard view).** The course now stands in the main hall of the Unity Asset
> Store pack "Warehouse - Abandoned Factory District 1.1" (ScansFactory), cleared around the flight volume (render-only
> scenery: no collision, no effect on the physics). The onboard camera is rendered by MuJoCo 3.16's Filament module on
> the CPU (Mesa lavapipe), lit by an image light distilled from an OVRTX path-traced light probe of the hall plus the
> sun, with per-class gains calibrated against OVRTX renders of the onboard view on the recorded oracle flight
> (`verify/fil_render.py`, byte-identical to `setup/runtime/fil_render.py`). The gates keep the task's colours, with
> worn-paint textures and twice the glow, so their colour cues hold under the hall's light. The scenery (meshes and
> 256 px textures), the course's textures and the two KTX maps (image light, sky) are fetched at image build from the
> benchmark's Hugging Face dataset, pinned in `image/assets.sha256`, into the root-owned `/opt/task-assets/payload/render`.
> A frame takes about 0.54 s on the task's two CPUs; the verify process peaks at about 1.3 GB (textures at 256 px, the
> heap trimmed after every scene). Measured: `ale validate` validate-aeacedc0, the oracle completed 7 of 8 fresh scenes
> (ratio 0.875, at or above `full_at` 0.85: reward 1.0), verify 22.9 min, zero collisions and aborts; the task's own tests pass.

**Draft: v7 onboard-camera switchback course, not yet qualified for admission.**
Version 5's external views were solved by Astra on 4/4 hidden scenes. Version 6's
onboard-only attempt timed out during authoring; its unchanged collected controller
then completed 3/4 fresh scenes in post-timeout replay. That replay is not a
completed native model run. Version 7 changes the route to require reversed
approaches and alternating heights. The task requires an image controller, not
operation solely through an in-context conversation.

## Input, Output and Success

The agent sees one forward-facing onboard RGB camera and remaining budget, explores using
collective thrust and attitude commands, and submits an executable `run.sh`.
The verifier runs it on four fresh private scenes. Success requires ordered
yellow/green/magenta/cyan gate crossings from their colored entry faces (also
indicated by white floor arrows), no collisions and two seconds of stable
hover above the goal. The score is normalized completion rate; speed is simulated
time, never host execution speed. `mean_gates_passed` and
`mean_gate_completion_fraction` expose partial progress separately from full
success, collisions, aborts and the canonical observation-dependence probes.

The camera moves and rotates with the drone. There are no external views, depth,
pose, velocity, supplied map or world-coordinate pixel calibration. A tall,
collidable cabinet occludes later gates and obstructs direct motion. Full-circle
yaw allows looking around. This is not a circular lap: the second gate faces
opposite to the first, followed by different headings and low/high passages.
Gate positions, heights and orientations vary by seed;
reverse/out-of-order crossings and goal-only shortcuts do not count. The blue
goal panel makes the final hover observable without a second camera.

The original 300-action / 60-simulated-second flight budget is unchanged. The
authoring timeout increases from 1800 to 3600 seconds to reduce timeout confounding.
Difficulty is not created by shrinking budgets.
`tools/privileged_reference.py` is explicitly a privileged state-feedback
feasibility check, never copied into a submission. It completed development
seeds 101-116 with zero collisions in 49.03-51.51 simulated seconds. This is not
an image-only solution, frontier result or substitute for `ale validate`.
The separate reference in `oracle/` uses only images at runtime but has disclosed
author-supplied design priors, including nominal route topology and safe transit
waypoints. It completed 26 development/regression/holdout scenes without collision.
Canonical calibration `validate-2f68303c`, oracle `c48dafab`, completed 4/4 new
scenes with no collisions/aborts and 70/92 aggregate observation probes. One
scene was 11/23, below half; the aggregate lock still passed. See
`verify/anchor.json` for provenance. No earlier-version result is reused.

## Interpretation Limits

- Canonical probes use the same live controller for real and counterfactual
  observations. A false observation may alter a stateful controller's later
  decisions. This task does not silently fork the shared grader; compare native
  scores with clearly labelled no-probe, matched-seed diagnostic replays before
  attributing a failure to physical difficulty. V6 replay improved from 3/4 with
  probes to 4/4 without them. A no-probe replay is not an official score.
- The verify stage has a 900-second aggregate wall-clock cap in addition to the
  10-second per-response deadline. Slow controllers may exhaust the aggregate
  cap. Shared Solver also restarts its read timeout on blank output; such hangs
  are protocol/robustness failures, not valid completed-scene difficulty evidence.
- Gate centers alternate heights, but their aperture ranges may overlap. The
  geometry does not prove that every seed requires altitude changes. The nominal
  reference uses them; difficulty claims must come from measured behavior.

## Reproduce

Use the ALE revision pinned by the repository's `vendor/ale` submodule. From the
repository root, with that engine installed:

```bash
TASK=tasks/uav/visual_low_level_flight_control
ale lint "$TASK"
python scripts/lint_domain.py "$TASK"
ale validate "$TASK" --runs-dir runs/crazyflie-validate
```

The image build fetches checksum-locked robot meshes. Execution is offline and
uses two CPUs, 4 GiB memory and no GPU. No model API is called by `ale validate`.
The successful submitted files, camera captures and interaction JSON are in the
oracle episode's collected artifacts. The trusted evaluation episode outcomes
and post-run replay seeds are in `trace.execution.jsonl` and `verification.json`.

For a host-side visual demo, install `image/requirements.txt`, `libosmesa6`, and
optionally `imageio==2.37.0 imageio-ffmpeg==0.6.0` for video:

```bash
python "$TASK/image/fetch_visual_assets.py" "$TASK/image/visual_assets.json" runs/crazyflie-assets
export MUJOCO_GL=osmesa LIBGL_ALWAYS_SOFTWARE=1 LP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
export VISUAL_ROBOTICS_ASSETS="$PWD/runs/crazyflie-assets"
python -m unittest discover -s "$TASK/tools/tests" -v
python "$TASK/tools/privileged_reference.py" --out runs/crazyflie-physical-check
python "$TASK/tools/review.py" --out runs/crazyflie-fpv-media --seeds 101 --mode privileged --video
python "$TASK/tools/review.py" --out runs/crazyflie-demo --seeds 101 102 103 --video
python "$TASK/tools/review.py" --out runs/crazyflie-random --seeds 101 102 103 --mode random
python "$TASK/tools/check_security.py" --ale "$(command -v ale)" --out runs/crazyflie-security
```

The preview/poster is a real simulator render. Its third-person reviewer camera
is not an additional agent observation. The video runs at five frames per second,
one frame per 0.2-second action, except a terminal action may finish early.
`route.png` shows the measured trajectory with reviewer-only labels over an
overhead capture unavailable to the agent. The inset is the true onboard image.
`actions.json` includes privileged positions for reviewers. `--mode privileged`
explicitly uses state; the ordinary reference mode only receives public observations.
Contract tests include rotated gate geometry, reverse/out-of-order crossings,
aperture clearance, camera attachment/viewpoint changes, full yaw, directional
markings, and a 32-layout check for return travel.
Development seeds above are not sealed benchmark test seeds. Keep run products
outside the task; do not commit videos, caches, fetched meshes or experiment logs.

## Model and Licence

The unscaled Crazyflie 2 model comes from
[MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/bitcraze_crazyflie_2),
under its MIT licence. The pinned download includes that licence, attribution,
source MJCF and visual/collision meshes. SHA-256 checks are mandatory.
The model is derived upstream from `whoenig/crazyflie_ros`.

We retain the nominal 0.027 kg mass, inertias and collision meshes. We add an
original occluded four-gate switchback course, one onboard camera and a bounded
attitude servo. Thrust/torque limits are benchmark abstractions, not measured
hardware limits. There is no firmware, rotor-speed, battery or real-world
validation claim. No geometry, textures or trajectories are copied from a
third-party drone-racing or rescue demonstration.

## Integrity and Remaining Admission Checks

The root-owned service owns simulation state and budgets; clients only receive
images. Reconnection and unsupported reset/report commands cannot restore budget
or claim success. The verifier stops the exploration service, freezes all agent
files read-only, then runs the submission as the unprivileged account. The
canonical `robotics_grader` and its observation-dependence check are unmodified.

Before admission: register/confirm the task in the coordination sheet, agree on
the exact frontier model and harness, measure its scores with adequate reasoning
budget, and tune genuine task challenges if it proves too easy. Keep the PR Draft
until the user explicitly requests otherwise. Do not interpret
environment/protocol failures as model inability.
