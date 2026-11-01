# HOT3D to Sharpa retargeting

Produce physically executable bimanual trajectories for the fixed HOT3D clips listed below.
In each clip a person moves one object with both hands; the robot must move it the same way.
The deliverables are open-loop sequences of absolute joint-position PD targets,
not a policy process and not recorded joint-state animations.

## Available material

For each clip, `/home/user/clips/<clip-id>/` contains:

- `frames/`: lossless RGB images in the original fisheye-camera orientation;
- `frames.jsonl`: the mapping from each 60 Hz control step to an image and its timestamps;
- `calibration.jsonl`: the preferred per-image MPS online calibration, including
  FISHEYE624 projection parameters and the row-major `T_Device_Camera` matrix;
- `camera_models.json`: the dataset calibration snapshot for reference;
- `headset.jsonl`: one row per exported image (joined on `camera_timestamp_ns` /
  `frame_file`) with the row-major 4x4 `T_Sim_Device`, the headset pose from the
  recording's MPS closed-loop SLAM expressed in the simulator world frame. The
  simulator world is z-up and in metres; it is the world frame of
  `assets/robot/sharpa/bimanual.xml` (forward kinematics of the 56 joint values),
  the `simulation environment local` frame of the control protocol, and the frame
  of the verifier's references. A camera pose is
  `T_Sim_Camera = T_Sim_Device @ T_Device_Camera`.

`/home/user/control_protocol.json` contains the shared 56-joint order, limits,
control period, and each clip's initial robot state. Sharpa robot assets and
simulator-side control code are in `/opt/flashdex`; the robot model is in
`assets/robot/sharpa/`.

Software, all usable offline and readable by you (inspect each environment's installed
packages yourself):

- `/opt/venv/bin/python3`: Python 3.12 with numpy (the format check below uses it);
- `/opt/isaac-venv/bin/python`: Python 3.11 with Isaac Sim and PyTorch (CUDA), the
  simulator stack of `/opt/flashdex`;
- `/opt/perception-venv/bin/python`: Python 3.12 with common scientific and
  computer-vision packages; pretrained model files are under `/opt/models`.

The verifier's fingertip reference is one point per finger, thumb to pinky, on the
fingertip surface of the person's hands (the fingertip vertices of HOT3D's MANO hand
annotations).

The verifier replays submissions with `/opt/flashdex/scripts/replay_hot3d_pd.py`
(run with `/opt/isaac-venv/bin/python`, one replay per process, the scoring window and
thresholds passed in its spec); it builds its scene offline. Its source and the rest
of the simulator stack are readable under `/opt/flashdex`.

One GPU is available. Object meshes, per-clip MANO parameters, object-pose references, support
geometry derived from those references, and reference control trajectories are
intentionally not provided. The clips are fixed; a general solution for arbitrary
videos is not required. Network access is blocked.

## Submissions

Write one file per clip, using exactly these names and shapes:

| File in `/home/user/submission/` | `motion_id` | `pd_targets` shape |
|---|---:|---:|
| `P0002_016222d1_seg016.npz` | 147 | `(164, 56)` |
| `P0002_016222d1_seg019.npz` | 150 | `(182, 56)` |
| `P0001_9b6feab7_seg003.npz` | 78 | `(166, 56)` |
| `P0002_2ea9af5b_seg013.npz` | 184 | `(206, 56)` |
| `P0001_624f2ba9_seg009.npz` | 43 | `(174, 56)` |
| `P0001_8d136980_seg005.npz` | 59 | `(252, 56)` |
| `P0001_624f2ba9_seg013.npz` | 47 | `(310, 56)` |
| `P0002_016222d1_seg018.npz` | 149 | `(196, 56)` |
| `P0009_37e30628_seg023.npz` | 606 | `(214, 56)` |

Each NPZ must be readable by `numpy.load(..., allow_pickle=False)` and contain:

- `pd_targets`: float32 absolute joint-position targets, in the protocol's exact
  joint order. All values must be finite and within the stated limits.
- `control_dt`: a scalar control period in seconds, corresponding to 60 Hz.
- `joint_names`: a one-dimensional string array with the protocol's 56 names in
  the same order.
- `motion_id`: the scalar integer listed for that clip above.

Each row is the desired joint position for one 60 Hz control step. The simulator
uses a 240 Hz physics step with decimation 4, so a row remains active for four
physics steps. PD targets are commands, not guaranteed physical states. Do not
submit MANO trajectories, normalized policy actions, or `joint_pos` samples.

## Evaluation

Each clip is reset to its own initial state and independently replayed by the
verifier under Isaac Sim / PhysX, without object-assist forces. The score is
computed from the resulting physical state, not from self-reported trajectories,
using time averages over the replay after a 15-step grace period.

Hand quality compares the robot's hands with the person's hands of the HOT3D
annotation (MANO), each hand separately, as distances in the world frame:

- the fingertip error, the mean over the five fingers of the distance from the robot's
  fingertip to the person's fingertip point (above), divided by 0.06 m;
- the finger-link error, the mean over the five fingers of the distances from the
  robot's knuckle and distal finger links to the person's MANO proximal and distal
  finger joints, divided by 0.08 m.

The larger of the two ratios, for the worse hand, is `w`, and hand quality is
`clamp((4 - w) / 3, 0, 1)`: full when both limits hold (`w <= 1`), falling linearly
to zero at four times the limits.

Object progress: let `e_static` be the object translation error of an object that
never leaves its initial pose, and `e` the replay's object translation error. The
progress is `1 - e / e_static`, and the progress factor is `clamp(progress / 0.85, 0, 1)`:
an object left where it started gives zero, reproducing at least 85% of the object's
motion gives one. The object's orientation is reported but not scored.

A replay scores `hand quality * (0.2 + 0.8 * progress factor)`: following the person's
hands without moving the object earns at most 0.2, and the rest requires reproducing the
object's motion. Each clip is replayed four times, each replay in its own Isaac process
with a scene of its own (replays never share a physics scene), and its score is the mean
of those replays; the task score is the mean of the clip scores. One replay starts
exactly from the clip's initial state; each of the other three starts from a small fixed
perturbation of it: the wrist translation joints move by up to 1 mm, every other joint
by up to 0.005 rad, and the object's start position by up to 1 mm along each horizontal
axis. These perturbations probe numerical robustness only; they are far smaller than the
uncertainty of anything estimated from the video. The same submission is replayed in all
four.

Keep `/home/user/submission` a plain directory that holds only regular files with
UTF-8 names: a symbolic link or a special file anywhere in it, or the directory missing
or replaced by a link, makes the whole run fail before any grading, and it is not
scored. Write each file with `numpy.savez` or `numpy.savez_compressed`; a file may be at
most 10 MB, and each of the four arrays at most 16 MB uncompressed. A missing,
unreadable or invalid file scores zero for its clip; it does not hide the other clips'
results. A replay that fails is run again after all the others, next to a control
replay of the same scene with a submission that holds the clip's initial pose: if the
control replays and the replay fails again, that replay scores zero; if the control
fails too, the simulator itself is not working, and the run is reported as an
infrastructure failure, not scored.
The initial robot state for each clip is public in the control protocol, while the
hidden scenes supply the objects and physical environment.

## Local format check

Use the ALE Python 3.12 environment explicitly:

```bash
/opt/venv/bin/python3 /home/user/practice_grader/grade.py
```

This checks the submission folder and every file against the grader's file rules and
the public control constraints, and lists anything that would make the whole run fail.
It does not need an object model and does not perform the hidden physical evaluation.
