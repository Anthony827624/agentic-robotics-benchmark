# Specify ten upgraded replicas of a desktop arm that is no longer on the bench

A lab built one six-servo desktop arm last spring (the "reference unit"). A customer now orders ten units with the
same kinematics, the same printed parts and the same mechanical stops, fitted with new servos, a new bus board and a
new power supply for a heavier, faster duty. The reference unit has been shipped to a partner lab and cannot be
measured again. What is left: a colleague's photo set of it on the workbench, the build log, the servo vendors'
datasheet pages, a distributor quote, two incoming-inspection reports of servo lots and the customer's brief.

You are the requirements engineer. Recover from this evidence what the replicas need (the arm's key dimensions, stops
and servo pockets, the working area, the payload, the loads the customer's acceptance duty puts on every joint, the
real capability of the servos you would buy), choose the actuators, the bus and the supply, and write it down traced to
the evidence. You are scored on the OUTCOME in the real world of that arm: how well your sketch replicates the real
reference unit, how close your derived loads are to the real ones, whether your BOM passes the customer's acceptance
procedure on the real arm with the real servo lot, and what it costs.

## What you have (`/home/user/`)

| path | content |
|---|---|
| `brief.md` | the customer brief, clauses `[B-01]..[B-16]`, including the ACCEPTANCE PROCEDURE (B-14, public and exact, including the model of a servo lot's capability), the deliverables and Appendix D (the geometric definitions and angle conventions of every graded quantity) |
| `build_log.md` | the colleague's notes `[BL-..]` on the build and on the photo session |
| `photos/PH-01.jpg .. PH-31.jpg`, `photos/photos.json` | the survey photo set as the colleague left it. `photos.json` lists per photo its public label and the focal-length class of the phone camera (main or 2x tele). Camera poses and intrinsics are NOT known and the lenses are not calibrated |
| `datasheets/DS-*.png` | vendor pages (images) of the servos, bus boards and PSUs of two vendors, plus the vendor's erratum sheet; nominal (typical) values in each vendor's own units at the vendor's voltages |
| `price_list.md` | the distributor quote: every orderable part id, role, body class, protocol, list price, and the lot the servos ship from |
| `lot_reports/LR-*.png` | two incoming-inspection reports (scanned pages, images only) of servo lots |
| `schemas/` | JSON schemas of the three deliverables (`requirements`, `bom`, `kinematic_sketch`) and their unit table, and `catalogue.schema.json` (the format in which the public grading modules take a transcription of the vendor pages) |
| `grader_public/` | the grader's own public modules (acceptance procedure, lot model, optimum search (the cheapest passing BOM), repair, every criterion). They contain no vendor, price or lot number: they take a catalogue in the format of `catalogue.schema.json` and an arm model as inputs, and carry nothing of the real arm |
| `schema_check.py` | the structure gate G0 on your submission (no ground truth involved) |

There is no checker of the real arm and no way to query it: the real arm, bench and servo lots exist only in the
grader, which uses the vendors' own numbers.

## What you deliver (`/home/user/submission/`)

- `requirements.yaml`: the traced requirements sheet. One item per requirement the documents or the photos fix
  (`id` REQ-..., `quantity` from the schema's vocabulary, `comparator`, `bound`, `unit`, `source_id` = the clause
  (`B-06`, `BL-03`), the photo (`PH-29`), the page (`DS-KM-S30-12`), the lot report (`LR-yyyy-nnnn` or `LR`) or
  `derived`) plus the derived items of brief B-16 for YOUR BOM (source `derived`, with a `derivation` note).
- `bom.json`: `bus_voltage_v` (7.5 or 12) and one line per part: one `servo` line with `count` 1 and its `joint` for each
  of `base_yaw`, `shoulder_pitch`, `elbow_pitch`, `wrist_pitch`, `wrist_roll`, `jaw`; the `bus_board` line(s); exactly
  one `psu` line with `count` 1. Every `part_id` from `price_list.md`.
- `kinematic_sketch.json` (SI: m, rad): `geometry` = the seven Appendix-D quantities (`shoulder_axis_height`,
  `shoulder_axis_offset`, `upper_arm_axis_distance`, `forearm_axis_distance`, `wrist_axis_to_tcp`, `jaw_lever`,
  `jaw_stroke`), each `{nominal, tol}`; `joint_ranges` = the mechanical stops `[lower, upper]` of the five arm joints in
  the Appendix-D angle conventions; `joints` = per joint `{servo_id (= the BOM line), body_class}` (`body_class` = the
  class of the POCKET at that joint: standard, compact or large); `links` = `link_1..3` `{min, max}` = nominal -+ tol of
  the upper-arm, forearm and wrist-to-TCP distances.
- `notes/` (optional): your method, code, intermediate estimates, your catalogue transcription. Recorded, never scored.

## How it is scored

Gates (multiply every criterion): **G0 structure** (the files parse and pass `schema_check.py`) and **G1 submission
integrity** (nothing in `/home/user/submission` may be written after your session ends, and the folder itself must stay
the one your session wrote: a symbolic link, or a folder replaced or renamed into place after the session, scores 0;
any process you leave running is stopped when grading starts). Then five criteria, each in [0, 1], weighted:

| criterion | weight | what is measured | band (linear between full and zero credit) |
|---|---|---|---|
| `kinematic_replication` | 0.30 | your sketch vs the real reference unit (Appendix D): lengths group (0.55 of the criterion) = the five lengths, stops group (0.30) = the ten stops of the five arm joints, gripper group (0.10) = jaw lever and stroke, body classes (0.05) = the six pocket classes, exact | lengths: full <= 0.5 mm, zero >= 4 mm; stops: full <= 1 deg, zero >= 6 deg; gripper: full <= 0.5 mm, zero >= 3 mm |
| `requirements_sheet` | 0.10 | facet F1 between your non-derived items and a hidden reference sheet valued in the real world (the brief's customer requirements incl. the payload band, and the working-area items the photos fix: `reach_radius_min`, `reach_radius_max`, `workspace_azimuth_min`, `workspace_azimuth_max`, `workspace_height_max`, `gripper_opening_min`); an item matches a reference item only with the same `quantity` AND the same `comparator` (`<` and `<=` count as the same comparator, a reversed one does not match); an item traced to a source other than the reference item's clause(s) keeps half its weight; invented or wrong-valued items lower the precision | brief items (incl. the payload band): relative error full <= 2 %, zero >= 4 %; photo lengths: full <= 1 %, zero >= 6 %; angles: full <= 1.5 deg, zero >= 6 deg |
| `derived_requirements` | 0.20 | the 15 derived items of B-16 (peak torque, duty-RMS load torque and peak speed of base_yaw, shoulder_pitch, elbow_pitch, wrist_pitch; grip torque; peak supply current; moving mass) vs their values in the real world with YOUR BOM installed on the real arm and the real delivered lot (inverse dynamics of the real arm over the real acceptance duty); a missing item scores 0 | relative error, normalised by max(true value, floor) with floors 0.05 N m, 0.2 rad/s, 0.2 A, 0.01 kg: full <= 3 %, zero >= 15 % |
| `bom_acceptance` | 0.10 | the acceptance procedure of B-14 run on the real arm with the weakest expected unit of the real delivered lot (B-14 e): the unit is accepted (1) iff all nine items pass (six servo joints incl. the jaw, PSU, bus / board, budget), else 0 | pass / fail |
| `bom_economy` | 0.30 | `max(0, 1 - (J_eff - J*) / 12 USD) x taper`: `J*` = the cheapest BOM that passes the acceptance procedure on the real arm with the real lot (recomputed live by the grader; an untested vendor counted as B-10 says, here and in `bom_acceptance`); `J_eff` = your BOM after the grader's deterministic repair: while an item fails, the failing line is replaced by the cheapest line that makes it pass (servo joints distal to proximal, then boards, then PSU; the bus voltage is never changed) and the brief's engineering-change cost is added per replaced line; 12 USD = the brief's engineering-change cost per line (B-13): a BOM one engineering change dearer than `J*` earns nothing; taper = 1 if `J_eff` <= the B-12 ceiling, else `max(0, 1 - (J_eff - ceiling) / (0.1 x ceiling))` | full at `J_eff` = `J*`, zero at `J*` + 12 USD |

`overall` = the weighted mean. The acceptance procedure and every formula above are public (brief B-14,
`grader_public/`); their inputs (the arm's geometry, pockets and masses, the working area, the payload, the vendors'
numbers, the lots' real capability) are not: they are behind the photos, the build log, the datasheet and erratum
pages, the quote and the lot reports.

## Notes

- Tools: Python 3.12 (`/opt/venv`, on PATH) with numpy, scipy, OpenCV (`cv2`, headless), scikit-image, MuJoCo 3.14,
  matplotlib, Pillow, PyYAML, jsonschema, trimesh. No network, no OCR engine. The photos and pages are large: process the
  photos programmatically and view only the images you need; read the document pages as images.
- Units: SI in the files (the schemas' unit table lists accepted equivalents such as mm, deg, g, kg.cm, rpm).
- Do not modify the given files in your home directory; the grader reads its own copies.
- Environment facts (harness limits): a single tool call whose input runs above roughly 25-30k tokens is dropped by the
  harness (it is never executed and no error comes back), and a response that reaches the 64k output-token limit is lost
  and, after at most a few retries, ends the session with whatever is in `/home/user/submission/` at that moment. Write
  large files in several parts (one function group per write), keep every single response well below that limit, write
  a first complete submission early and refine it, and run `schema_check.py` after each part.
