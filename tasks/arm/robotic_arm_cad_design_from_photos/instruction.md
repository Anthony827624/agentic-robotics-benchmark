# Re-model a modified six-servo desktop arm in FreeCAD from a photo set

You are the mechanical designer of a lab that builds small six-servo desktop arms. One unit was modified by a student
(link lengths, heights and offsets changed, cut-outs added, printed spacer blocks inside some servo pockets) and its CAD
is lost. Before the unit was shipped to a partner lab, a colleague took it apart and photographed everything on the
workbench with a phone: the ten printed parts laid out on a cutting mat, each part turned over once, a few close-ups,
then the re-assembled unit in three poses and its gripper closed and open. The photos are all there is: nobody can
measure the unit again. Re-create the unit in FreeCAD so it can be re-printed and simulated: the ten printed parts as
solids around the bought servos, assembled with the unit's joints.

Your work is graded against the photographed unit's ground truth, which only the grader has: how well your mechanism,
your parts' features and shapes, their masses and centres of mass match the unit's, and whether the result works
(servos fit and are retained, no self-collision over the unit's range of motion).

## How you work

- Do the work in FreeCAD through the FreeCAD MCP server `freecad`: it drives the FreeCAD 1.1 session running on this
  machine (documents, objects, views, and `execute_code` / `execute_code_async`, which run Python inside that session,
  with FreeCAD's modules: Part, PartDesign, Sketcher, Assembly, Import, Qt; its state persists between calls). Build the
  parts, place the servos, set up the assembly and save the document there, as a designer working in FreeCAD would.
- Your other tools (reading files and images, a shell) are there for everything around it: looking at the photos and the
  servo's documents, cropping a detail, notes and checks. There is no internet access. In the shell, the image's Python
  with its scientific packages (numpy, scipy, Pillow, trimesh and others) is `/opt/venv/bin/python3`; the `python3` on
  your `PATH` may be a bare interpreter.
- `get_view` and the screenshots the object tools return show FreeCAD's 3D view of the session. `Read` shows a photo
  scaled down to fit; to look at a detail at full resolution, crop it to a file first and read the crop. Keep scratch
  files under `/home/user/work/`.

## Given inputs (in `/home/user/`)

**`photos/photo_01.jpg` ... `photo_80.jpg`**: the fixed photo set of the unit. A paper tag printed with the part's id
(`P01` ... `P10`) lies next to every part in the teardown photos.

| photos | what the photographer recorded |
|---|---|
| 01-06 | teardown: parts P01, P02 on the mat, first side up, six directions |
| 07-12 | the same parts turned over onto another face |
| 13-18 / 19-24 | P03, P04: first side / turned over |
| 25-30 / 31-36 | P05, P06, P07: first side / turned over |
| 37-42 / 43-48 | P08, P09, P10: first side / turned over |
| 49-58 | close-ups of pockets, horn plates, the base underside and cut-outs, the parts laid out as: 49-50, 54-55, 58 P05-P07 (first side); 51, 56 P01-P02 (turned over); 52-53 P01-P02 (first side); 57 P03-P04 (turned over) |
| 59-64 | the re-assembled unit on the bench top (the surface it is mounted on), configuration **Z**: every servo commanded to its zero position |
| 65-70 | configuration A (joint values not recorded) |
| 71-76 | configuration B (joint values not recorded; the jaw is open) |
| 77-78 / 79-80 | the gripper close to the A4 board, wrist down: jaw at its zero position (closed) / jaw open |

All photos come from one phone camera (wide lens, about 20-30 mm 35 mm-equivalent; not calibrated; no camera pose was
recorded; exposure and white balance vary a little). The servos are installed and the cables routed along the links in
the assembled photos. Scale references, physical objects in the scene, with their nominal sizes:

- cutting mat: grey, A3 (420 x 297 mm), printed grid every 10 mm with every fifth line bold, numbered in cm; 2 mm thick, the
  parts lie on its top face;
- checker card: 10 x 7 squares of 12 mm (9 x 6 inner corners), 6 mm white margin, on a 132 x 96 x 1 mm card lying flat on
  the mat in every teardown and close-up photo;
- A4 checker board: 10 x 7 squares of 25 mm (9 x 6 inner corners), 10 mm white margin, on a 270 x 195 x 3 mm board standing
  beside the assembled unit;
- two steel rulers (300 mm scale with 1 mm ticks, 316 x 25 x 0.9 mm), one on the mat, one on the bench;
- an ID-1 card (bank-card size, 85.6 x 53.98 mm, 0.76 mm thick) on the bench.

**`servo/`**: the bought servo (the same part at every joint): `datasheet_BUS-SERVO-12V-A.png`,
`drawing_BUS-SERVO-12V-A.png` (dimensioned outline), `servo.step` (vendor model) and `servo_keepout.step` (the servo
without its detachable rear disc: the envelope the grader intersects with your parts). In the servo's own frame, +z is
the horn axis (through the origin), z = 0 is the bottom face of the case and the horn's mating face is at z = 33.1 mm.
Datasheet numbers: case 45.4 x 24.8 x 28.8 mm between the flanges, flanges 1.5 mm; horn axis 12.5 mm from the case centre
along its length; horn disc dia. 20.0 mm with four 2.5 mm bolt holes on a 14.0 mm square-pattern PCD; flange holes dia.
1.5 mm (M2 self-tapping screws); passive rear disc dia. 20.0 mm; mass 60 g.

## Deliverable: `/home/user/submission/arm.FCStd`

One FreeCAD document, saved there, holding:

1. **Ten PartDesign Bodies**, one per printed part, each labelled with the id on its paper tag (`P01` ... `P10`), each one
   valid solid, in millimetres.
2. **The six servos**: `servo/servo.step` imported six times, each copy placed where that servo is mounted.
3. **One Assembly** (FreeCAD's Assembly workbench: an Assembly object with its joints) holding every part and servo: a Grounded joint on the base; Fixed joints for
   everything rigidly attached (parts screwed together, each servo to the part it is screwed into); one Revolute joint
   for every joint of the arm between the two parts it connects, with both angle limits enabled (Min / Max angle) at the
   range the unit moves through without self-collision, measured from the saved pose.
4. **Saved in configuration Z**, the pose of photos 59-64.

The grader reads the document as saved; anything not saved in `arm.FCStd` is not seen.

## How it is graded

The grader reads your document with FreeCAD, works out your mechanism from it (parts by their labels, servos by their
shape, links = parts and servos held together by Fixed joints, the Revolute joints between them), places your grounded
base onto the unit's base (the base rotation axis, the surface it stands on, and the turn about that axis that fits the
base parts best) and compares everything with the unit at configuration Z. A joint's positive sense is its driving
servo's positive rotation about its own +z; its zero is the saved pose. The tool point is built by the grader on your
P09: on the face of its finger that looks at the moving jaw, at the middle of the finger's width, 10.0 mm below the
fingertip.

**Gates** (each 0 or 1, multiplied into every score):

- `cad_valid`: the document opens; ten bodies labelled P01 ... P10, one valid watertight solid each; six copies of the
  given servo; the Assembly forms one serial chain of revolute joints from the grounded base, with the same number of
  joints as the unit, whose links carry the same parts as the unit's links (a part in the wrong link, a missing joint or a
  component not connected to the base fails this gate); the parts convert into a valid MuJoCo model; the document was not
  written after the session ended.
- `public_model_reuse`: no part is a published model of an arm.
- `cad_json_consistent`: each revolute joint's axis lies on the horn axis of its driving servo (the servo of the parent
  link whose horn axis is closest to the joint axis) within 1 deg and 0.5 mm, and each child link rests on its parent link
  or on the driving servo (gap at most 0.2 mm).

Also reported, not a gate: `servos_mounted` (every servo free of part material (keep-out), held by screws through at
least two of its flange holes, its horn's four bolts and centre landing on the driven link); servo retention is scored
in `component_fit`.

**Criteria** (weights), every band absolute against the unit: band(e; full, zero) = 1 at |e| <= full, 0 at |e| >= zero,
linear between.

- `kinematic_fidelity` (0.30): joint positions (each driving horn centre, 0.3 / 3 mm), six key lengths of the chain
  (0.3 / 3 mm), the tool point and the moving fingertip over 64 random poses inside the unit's range (median 0.5 / 8 mm,
  90th percentile 1 / 15 mm), and how much of the unit's range your limits cover.
- `feature_fidelity` (0.25): cut-outs (position 0.3 / 3 mm, area 10 / 50 %), servo pocket floors (0.3 / 3 mm), each part's
  extents (0.3 / 3 mm), the holes in the base's underside (0.3 / 3 mm).
- `shape_fidelity` (0.25): each part against the unit's after a rigid alignment: 90th-percentile surface distance
  (0.3 / 2.5 mm) and volume IoU (1 - IoU 0.05 / 0.40).
- `mass_properties` (0.10): each link's mass (solid volume x 520 kg/m3 + 60 g per servo it carries; 2 / 15 %) and centre
  of mass at configuration Z (0.5 / 6 mm).
- `component_fit` (0.05): how well each servo is free of part material, retained and its rear side supported.
- `interference_free` (0.05): no self-collision between links over the unit's range and your declared range.

Floors: the mean over the parts of clamp((IoU - 0.20) / 0.30, 0, 1) multiplies `kinematic_fidelity` and
`feature_fidelity`; that times band(median tool-point error; 15, 60 mm) multiplies `mass_properties`, `component_fit` and
`interference_free`. The photographed unit itself scores 1.0 on everything.

## Practical notes

- A servo may sit on a printed spacer block at the far end of its pocket; the horn mates at any spline angle.
- Session time: this session is stopped after 6 hours of wall-clock time from its start. A session stopped by that limit
  is not graded at all, so a late finish scores nothing. Note the time when you begin (for example print
  `datetime.datetime.now()` through `execute_code`), save a complete document early and keep saving it, and end the
  session yourself (stop working and say that you are done) at least 15 minutes before the limit.
- Environment fact (harness limit): a response that reaches the 64k output-token limit is lost and, after at most a few
  retries, ends the session with whatever is in `/home/user/submission/` at that moment. Keep every single response
  well below that limit.
