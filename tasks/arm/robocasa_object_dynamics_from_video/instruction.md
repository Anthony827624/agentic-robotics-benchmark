# Object dynamics from video (RoboCasa kitchen)

A Franka Panda robot in a kitchen holds a rod with a 16 mm ball tip and pokes household objects standing on the
counter. Each object has a hidden, non-uniform mass distribution: a thin shell, contents that may fill the object
from the bottom up or sit in its top part (they move rigidly with the object), and a hidden internal weight.
You only have **RGB video** of these interactions. Estimate, for each of the three test
objects **A**, **B** and **C**, its centre of mass, inertia tensor and object-counter friction coefficient. Your
estimates are scored by how well a robot that plans with them does on new physical tasks with the real objects.

You have 8 hours, 4 CPU cores, 8 GB of memory (processes that exceed it are killed), no GPU and no network. Python 3.12 is in `/opt/venv` (NumPy, SciPy, OpenCV
(headless, with FFmpeg), Pillow, trimesh, scikit-image, MuJoCo 3.16.0, and the robosuite 1.5.2 asset files for the robot model; robosuite's own object meshes are unrelated to the objects here). MuJoCo offscreen rendering works out of the box (`MUJOCO_GL=osmesa`, CPU).

## What you get (`/home/user`)

```
data/cameras.json                     three fixed, calibrated cameras (cam0, cam1, cam2)
data/objects/<ID>/object.json         ID in P (practice), A, B, C: total mass (kg), number of clips
data/objects/<ID>/clipNN/cam0.mp4     RGB video, 640x480, 30 fps, H.264 (also cam1.mp4, cam2.mp4)
data/objects/<ID>/clipNN/clip.json    clip kind, fps, number of frames, stop-bar pose (or null)
data/objects/<ID>/clipNN/probe_500hz.npy   commanded ball-centre positions (M,3), one row per 2 ms physics step
data/objects/<ID>/clipNN/arm_q.npy    the arm's 7 joint angles per video frame (N,7)
data/objects/P/gt_params.json         ground truth of the practice object P, in the submission format
rphys.py, rtasks.py                   the physics world and the scoring task code used by the grader (byte-identical)
rarm.py                               the robot's visual model (for example to render robot masks)
rio.py                                I/O helpers (video loading, cameras, projection, writing the submission)
submission/                           write params.json here
```

- **Cameras.** `cameras.json` gives, per camera, the OpenCV pinhole model: pixel ~ K (R_w2c X + t_w2c), no lens
  distortion. World frame: the counter top is the plane z = 0, z up, metres. The calibration is exact.
- **Videos.** Rendered images of a kitchen with clutter; the arm occludes parts of the scene. They are degraded like a
  real camera: motion blur, optical blur, vignetting, white balance and exposure differences between cameras, sensor
  noise, and video compression. Frame k of a clip is at time k/30 s; frame 0 is at probe sample 0.
- **The only information about the objects is in the images.** You get no meshes, textures, dimensions, poses or
  trajectories of the objects. The total mass of each object is given (a scale reading).
- **Clips.** Each object has 10 clips. At the start of every clip the object stands upright at rest at a new place and
  orientation near the counter origin. Kinds: `tip` (slow push high on the object while a stop bar lies beyond the far
  side of the object, so the object slides against the bar and pivots over its base edge or the bar; some clips topple
  the object, some do not), `rock` (a short fast push at mid height with the stop bar placed the same way),
  `slide` (a fast push at the base, 12 mm above the counter, with a lateral offset, then the probe stops and the
  object slides and turns freely). The probe programs were planned only from the robot's rough perception of each object: a position
  estimate (about 1 cm error) and a vertical box/cylinder model whose radius and height are off by about 8% and 10%.
  Contact heights are absolute (tip 8.5-12.5 cm, rock 6.5-9.5 cm, slide 1.2 cm above the counter), the stop bar is placed
  10-35 mm beyond the far side of that rough model (moved farther if it touched the object), and the probe backs off
  if its start point touches the object. So the programs carry no precise information about the object.
- **Practice object P** (a cereal box from the RoboCasa asset set) has the same kinds of clips, and its true parameters are in
  `gt_params.json`. Use it to check your conventions and pipeline. P is not scored.

## The physics (identical in your copy and in the grader's world)

`rphys.py` defines the world exactly as the grader simulates it: MuJoCo 3.16.0, timestep 2 ms, `implicitfast`,
elliptic friction cones, `multiccd`, default contact softness; the counter is a plane at z = 0; the probe is a
sphere of radius 8 mm whose position and velocity follow `probe_500hz.npy` every step (it is not pushed back by
objects); probe-object friction 0.4; the stop bar is a 24 x 1 x 1 cm box lying on the counter (friction 1.0);
pins (scoring only) are vertical cylinders, 4 cm tall, friction 1.0, contact solref (0.004, 1). The object is a free
rigid body: its collision shape is the union of convex pieces (RoboCasa's convex decomposition of the real object),
with an explicit mass, COM and inertia tensor, and the object-counter sliding friction mu (torsional 0.005).
`rphys.Sim` builds this world from **your** convex pieces, inertial parameters and mu and replays a probe program, so
you can simulate any clip with your own model. Everything except the objects' geometry, mass distribution and
friction is the same as in the videos.

## Deliverable

`/home/user/submission/params.json` (`rio.write_params` writes it):

```json
{"objects": {
  "A": {"com": [x, y, z], "inertia": [[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]], "mu": 0.5},
  "B": {...}, "C": {...}}}
```

- Everything is expressed in the **world frame at the object's reference pose = its rest pose in frame 0 of clip00**:
  `com` is the centre-of-mass position in metres; `inertia` is the inertia tensor about the COM, in kg m^2, with world
  axes, for that pose; `mu` is the object-counter sliding friction. The grader moves your values with the object's
  true reference pose into the object's own frame. See P's `gt_params.json` for an example.
- Rules: numbers only; the inertia must be symmetric, positive definite and satisfy the triangle inequality, else that
  object scores 0; the COM is clamped to the object's bounding box plus 1 cm; mu is clamped to [0.1, 1.2]. A missing,
  unreadable or non-conforming file, a symlink or a special file scores 0.

## Scoring

For each test object the grader runs a fixed set of hidden tests (directions, heights, placements and targets are
hidden). Every action is planned by the code in `rtasks.py` on a model made of the object's **true** collision shape and
mass with **your** COM, inertia and mu, and then executed once in the true world:

| Family | What the robot does | Score per test |
|---|---|---|
| pin | stands the object upright on pins of radius 3, 5, 7 and 10 mm with your COM over the pin axis, and lets go for 3 s (`test_pin`) | fraction of pins on which it stays up (tilt < 15 deg) |
| tip | slow push (5 cm/s) high on the object against the stop bar; stroke = 0.93 x the first stroke that topples the object in your model (`test_tip`) | 0 if the real object topples; otherwise rises linearly from 0 at 80% to 1 at >= 90% of the real object's first toppling stroke |
| rock | short fast push (15 m/s^2, 1 cm contact) at mid height; speed = 0.93 x the first speed that topples it in your model (`test_rock`) | same rule on speed |
| push | push at the base with a lateral offset of 12-25 mm; speed chosen so that your model slides the object a target distance of 5-22 cm (`test_push`) | min of q(along-track error, 10 mm), q(error of your predicted sideways drift, 10 mm), q(error of your predicted rotation, 5 deg), q = clip(2 - error/tol, 0, 1): the final pose must match in every coordinate |

An object may lack a family (when no robust test of that kind exists for it). An object's score is the mean of its family scores; the metric `object_dynamics_score` is the mean over A, B and C
(higher is better, 1.0 = every test passed). Mass, gravity, the probe and the physics settings always come from the
grader; nothing you write is executed.
