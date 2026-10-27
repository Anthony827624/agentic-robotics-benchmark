# Assembly cell API (`cell`), version arm-full-assembly-cell/1.0.0

Your program `/home/user/submission/assemble.py` exposes `solve(cell) -> None`. The grader runs it once per hidden
episode in a fresh process under a dedicated unprivileged account, from a per-run directory holding a root-owned
READ-ONLY copy of your submission folder (regular files only: symbolic links and `__pycache__` are not copied), with an
empty writable `$HOME` / `$TMPDIR`, a minimal environment (`PATH`, `HOME`, `TMPDIR`, `LANG`, `MUJOCO_GL=osmesa`,
`PYTHONPATH` = a read-only copy of `~/given/lib`, `ARB_GIVEN_DIR` = a read-only copy of `~/given`, BLAS threads 2) and the
launcher `python3 -B -E -s -c <bootstrap> <lib> assemble` (the bootstrap puts `<lib>` on `sys.path` and calls
`arm.assembly_info.client.main`, which imports `assemble` from the current directory), or `bash run.sh` when you ship one.
`cell` is a proxy (`arm.assembly_info.client.InfoCell`): every method is one JSON-lines request on the process's original
stdout, answered on stdin by the grader's physics server; `print()` goes to stderr.

Units on the API: metres, radians, quaternions `wxyz`; the cell frame = the bench-top centre, z up (see
`cell_manual.md`). The given part files are in millimetres.

| call | returns | notes |
|---|---|---|
| `describe()` | `robot` (model, base pose, joint names / ranges, home posture, tcp offset, gripper, force-sensor note), `control` (tick, rate limits, stiffness range, modes), `fastening` (the rule and its numbers), `cameras` (name -> resolution, rgb / depth, nominal horizontal field of view, mount description), `camera_budget_frames`, `datums` (the documented boards and the fixture's nominal position, as in `cell_manual.md`), `items` (what is on the bench, as text), `trays` (none), `budgets`, `safety` | static for the episode; no part poses, no mates, no fixture pose |
| `get_state()` | `sim_time`, `done`, `robot {q[7], dq[7], cmd_q[7], gripper_width, tcp_pos[3], tcp_quat[4], wrench[6], mode, stiffness_scale}` | joint angles, the tcp pose and the gripper width are exact; `wrench` (force N, torque N m, at the hand flange in the hand frame) carries white noise (~0.3 N / 0.02 N m rms per reading) and an unknown per-episode bias (up to ~1 N / 0.05 N m), and includes the hand's own weight: tare it |
| `set_control(mode, stiffness_scale)` | ok | `mode` in `joint`, `cartesian`; `stiffness_scale` in [0.2, 1.0] scales the seven joint position gains |
| `command(q=[7])` or `command(tcp_pos=[3], tcp_quat=[4])`, optional `width` | ok / error | targets are held until the next command; joint targets are clamped to the ranges and rate-limited to 1.5 rad/s; Cartesian targets go through the cell's damped-least-squares differential IK on the `tcp` site (<= 0.5 m/s, 1.5 rad/s, joint rate limit); the gripper `width` (m, 0..0.08) ramps at 0.05 m/s; grip force = 2500 N/m x (object width - target width) / 2 per finger, <= 60 N |
| `step(n_ticks)` | `sim_time`, `done`, `events[]` | advances n x 10 ms of physics (1 kHz inside); nothing moves outside `step`; n <= 500 per call; events: only `safety:<what>` and `episode_over` (no "seated", "grasped" or "dropped" events) |
| `get_camera(name, depth=True)` | `camera`, `frame_index`, `width`, `height`, `format` (`png`), `rgb_png_b64`, and with depth `sensor_depth_u16_mm_b64`, `depth_unit_m` (0.001), `depth_invalid_value` (0) | one frame of `top` or `wrist` (both RGB-D) rendered at the current physics state: the PNG colour image and the depth sensor's 16-bit PNG (depth along the optical axis, millimetres, registered to the colour pixels, 0 = no reading); no intrinsics, no pose, no exact depth; at most 600 frames per episode over all cameras, then `{"error": "camera_budget"}`; rendering takes about 0.3-0.8 s of wall clock per frame and counts against the episode's wall-clock cap |
| `finish()` | ok | ends the episode; the grader then reads which mates the cell has fastened |

`arm.assembly_info.client.decode_frame(reply)` returns `(rgb uint8 H x W x 3, depth float32 H x W metres, NaN = no reading)`.

Anything else answers `{"error": "forbidden"}`, counts as an illegal call and does nothing. In particular
`get_state_privileged()`, `describe_privileged()` and `reference_assemble()` (exact part poses, mate status, the mating graph, the grader's reference program), `reset(seed)` and
`truth()` are not available to your program.

## Budgets and aborts

1200 s of simulated time and 2400 s of wall clock per episode (rendering included); 120 s of silence between two requests
aborts the episode; a CPU-time backstop of 4 x the wall cap applies per process; every process of the run's account is
killed when the episode ends; a program that breaks the wire (crash, malformed line, exit) ends ITS episode with what it
achieved so far and the other episodes still run.
