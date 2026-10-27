# Customer brief: ten replica units of the lab's desktop arm (upgraded duty)

Clause ids in square brackets are the trace ids of your requirements sheet (`source_id`).

[B-01] Context. The customer orders 10 units that replicate the lab's reference unit (the six-servo desktop arm shown in
the survey photos PH-01..PH-31 and described in the build log BL): the same kinematics, the same printed parts and the
same mechanical stops (five revolute arm joints plus the jaw), fitted with NEW servos, a new bus board and a new power
supply for a heavier, faster duty. The reference unit itself has been shipped away and cannot be measured again.

[B-02] Precedence. This brief > the build log > the lot reports > the datasheets. An erratum sheet overrides the datasheet
it corrects. Lot data describe the lot they were measured on; the pilot servos come from the lot named in the quote.

[B-03] Units. Deliverables use SI units (m, rad, kg, s, N m, rad/s, A, V); the unit table of the schemas lists the accepted
equivalents (mm, deg, g, kg.cm, rpm, ...).

[B-04] Working area. Every target lies inside the taped area on the bench, i.e. inside the tape's INNER edges (photo PH-29):
an annular sector about the base-yaw axis, bounded by two arcs and two radial edges.

[B-05] Heights and approach. Target heights run from 20 mm above the bench top to the top of the grey tote plus 30 mm
(PH-29). At every target the tool approaches at an approach pitch of -55 deg (the elevation angle of the TCP +z axis,
measured in the arm's vertical plane, negative = pointing below the horizontal, away from the base; Appendix D), with
roll 0.

[B-06] Payload. The customer's objects stand in PH-30 and PH-31. The top of the payload band is the mass of the heaviest
object, which the customer put on a kitchen scale for photo PH-31 (read the mass off the scale's display); the bottom of
the band is 0.19 kg. Centre of mass up to 25 mm beyond the TCP along the tool (+z) axis.

[B-07] Motion. Every move between two targets takes 0.91 s (joint-space trapezoid with equal thirds: accelerate,
cruise, decelerate); the arm holds 1.0 s at every target; the duty is continuous (an endless sequence of one move and
one hold, no rest).

[B-08] Proof test. 1.25 x the top of the payload band is held at every target with the arm stationary.

[B-09] Grip. Pad friction coefficient 0.5 on two pads, safety factor 2 against slip at the largest vertical acceleration
of the moves; the jaw holds the object for the whole transport (size the jaw on its RATED torque); the jaw closes its full
stroke (open stop to closed) within 0.5 s at no load. The gripper opens at least 10 mm wider than the widest object of
PH-30 and PH-31.

[B-10] Servos. From the vendor pages DS-* only. The servo body must fit the replicated pocket at that joint (the pockets are
the reference unit's, see PH-26, PH-27 and PH-28): one servo per joint, no dual servos. A vendor without an
incoming-inspection report is counted at no more than 70 % of its typical (datasheet) stall torque, no-load speed
and rated torque, on the datasheet's straight-line torque-speed characteristic.

[B-11] Power. One bus at either 7.5 V or 12 V; one power supply (PSU) per unit; the harness voltage drop of BL-03 applies
at every servo; no current limiting during any acceptance move.

[B-12] Cost. At most 156 USD per unit for servos + bus boards + PSU at the 10-99 pcs list price (price_list.md).

[B-13] Engineering change. Any BOM line changed after the pilot acceptance costs 12 USD per unit (rework and
re-qualification).

[B-14] Acceptance procedure (the customer runs it on a pilot unit; public, verbatim):
  a) Target grid: radius r in 6 equal steps over [r_min, r_max] of the taped area, azimuth in 7 equal steps over its
     angular span, height z in {lowest, midpoint, highest} of B-05, approach pitch -55 deg of B-05, roll 0:
     126 TCP targets in the grid order (r, azimuth, z). Joint angles by inverse kinematics elbow-up (the
     elbow-pitch axis above the line from the shoulder-pitch axis to the wrist-pitch axis), inside the stops.
  b) Holds: every grid target with the payload band top at the CoM offset of B-06, static.
     Proof holds: every grid target with 1.25 x the band top, static, stall torque only.
  c) Moves: for each of base_yaw, shoulder_pitch, elbow_pitch, wrist_pitch the 12 not yet selected target pairs with the
     largest joint-angle change of that joint (ties: the pair with the lowest indices in the grid order), plus the
     corner pairs (for each approach pitch: the 4 space diagonals and the 4 azimuth edges of the corner box
     r in {min, max} x azimuth in {min, max} x z in {lowest, highest}), each pair once, each run in both directions
     with the B-07 trapezoid and the payload band top.
  d) Loads: rigid-body inverse dynamics of the arm (printed parts + the BOM's servos as point masses with their box
     inertia at the pockets + the payload); tau_j = the load torque at joint j (excludes the joint's own output inertia
     J_out of its datasheet, which the pass rule adds); qd_j, qdd_j from the trapezoid.
  e) Capability of a SKU, for the weakest expected unit of the lot the pilot servos come from. The servo sees
     V_s = V_PSU - (the harness drop of BL-03). Datasheet stall torque, no-load speed and stall current at V_s by linear
     interpolation between the datasheet's two voltage points (a page with one voltage point: in proportion to the
     voltage). A unit's ratio to the datasheet (measured / datasheet at the same voltage) is, for stall torque:
     f_stall x k_SKU x (1 + s z1), for no-load speed: f_speed x (1 + s z2), for rated (continuous) torque:
     f_cont x k_SKU x (1 + s z3), with z1..z3 independent standard normal per unit. The lot factors f_stall, f_speed,
     f_cont, the lot spread s and the lot droop d (below) belong to one lot; the SKU factor k_SKU belongs to the SKU's
     design and is the same in every lot of that vendor. The weakest expected unit is the lot's 2.5th percentile:
     z = -1.96 on every factor: tau_s = stall(V_s) x f_stall x k_SKU x (1 - 1.96 s), w0 = no-load(V_s) x f_speed x
     (1 - 1.96 s), tau_c = rated x f_cont x k_SKU x (1 - 1.96 s). Torque-speed characteristic of every unit of a lot:
     w(tau) = w0 x (1 - tau / tau_s) x (1 - d x tau / tau_s), d >= 0 (d = 0 is the datasheet's straight line); the
     torque available at speed w is the tau at which w(tau) = |w|. Current: I = I0 + |tau| (I_stall - I0) / tau_s. A
     vendor without inspection data: B-10.
  f) Joint j passes iff at every move sample |tau_j| + J_out |qdd_j| <= the torque available at |qd_j|; every hold and
     every proof hold has |tau_j| <= tau_s; the duty RMS sqrt(s_move x max over moves of mean((tau_j + J_out qdd_j)^2) +
     s_hold x max over targets of tau_hold^2) <= tau_c, with s_move = T / (T + H) and s_hold = 1 - s_move; the servo's
     voltage range contains the bus voltage; and its body fits the pocket.
  g) Gripper passes iff the jaw servo's tau_c >= F x r_J with F = m_top x (g + a_z,max) x 2 / (2 x 0.5) (a_z,max: the
     largest vertical TCP acceleration over the moves, r_J: the jaw lever of Appendix D), w0 >= jaw stroke angle /
     0.5 s, its voltage range contains the bus voltage and its body fits.
  h) PSU passes iff its voltage is the bus voltage, every servo's voltage range contains it, and its rated current >= the
     peak over time of the summed joint currents of e) during any move (the jaw draws its holding current at F x r_J).
  i) Bus / board passes iff the BOM's boards serve every servo protocol with the bus voltage inside their input range.
  j) Budget passes iff the BOM list price (servos + boards + PSU) <= the B-12 ceiling.
  The unit is accepted iff all nine items pass (six servo joints incl. the jaw, PSU, bus / board, budget). A failed item
  is fixed by an engineering change (B-13).

[B-15] Conventions: Appendix D below (verbatim part of the brief).

[B-16] Deliverables: `requirements.yaml`, `bom.json`, `kinematic_sketch.json` (schemas in `schemas/`). The requirements
sheet reports, with `source_id: derived` and a `derivation` note, the derived items of the acceptance duty for YOUR BOM
installed on the replica, each as the value the acceptance procedure produces: `joint_peak_torque:<joint>` (max |tau_j|
over the move samples and the holds), `joint_rms_torque:<joint>` (the duty RMS of the LOAD torque tau_j, without J_out),
`joint_peak_speed:<joint>` (max |qd_j| over the moves), each for base_yaw, shoulder_pitch, elbow_pitch and wrist_pitch;
`grip_torque` (F x r_J of B-14 g), `supply_peak_current` (the peak summed current of B-14 h with your BOM and the lot's
weakest expected unit), `moving_mass` (everything that moves with the base-yaw joint: the printed parts above the base plus
your servos of the shoulder, elbow, wrist-pitch, wrist-roll and jaw joints; no payload).

## Appendix D. Geometric definitions

Base frame: origin where the base-yaw axis meets the mounting plane (the base's bottom face on the bench top), z up, +y along
the arrow on the bench in front of the base (yaw 0), +x = +y x +z (to the right). At yaw 0 the pitch axes are parallel to x.
TCP: the point on the fixed finger's gripping face, on its mid-line, 10.0 mm back from the fingertip, rigid on the gripper
(+z approach along the finger, +x the face normal towards the moving jaw).

| quantity | definition |
|---|---|
| `shoulder_axis_height` | height of the (horizontal) shoulder-pitch axis above the mounting plane |
| `shoulder_axis_offset` | shortest distance between the base-yaw axis and the shoulder-pitch axis, + when the shoulder axis lies on the +y side at yaw 0 |
| `upper_arm_axis_distance` | distance between the parallel shoulder-pitch and elbow-pitch axes |
| `forearm_axis_distance` | distance between the parallel elbow-pitch and wrist-pitch axes |
| `wrist_axis_to_tcp` | distance from the wrist-pitch axis to the TCP |
| `jaw_lever` | distance from the jaw hinge axis to the moving jaw's tip centre |
| `jaw_stroke` | distance between the two fingertip centres at the jaw's open stop |
| base_yaw angle | rotation of the arm plane about +z from +y (right-hand) |
| shoulder_pitch angle | angle of the vector shoulder axis -> elbow axis (their common perpendicular) from +z, right-hand about the pitch-axis direction (+x at yaw 0): reaching forward is negative |
| elbow_pitch angle | angle from the shoulder -> elbow vector to the elbow -> wrist vector, right-hand about the pitch-axis direction |
| wrist_pitch angle | angle from the elbow -> wrist vector to the wrist axis -> TCP vector (the perpendicular from the wrist-pitch axis to the TCP), same sign rule |
| wrist_roll angle | rotation of the jaw hinge axis about the wrist-roll (gripper rotation) axis, directed from the wrist towards the fingertips, 0 when the hinge axis is parallel to the pitch axes (either sense), right-hand about that axis |
| stops | `[lower, upper]` of each arm joint in these angle conventions (the mechanical stops = the servos' programmed angle limits of the reference unit, PH-13..PH-22) |
