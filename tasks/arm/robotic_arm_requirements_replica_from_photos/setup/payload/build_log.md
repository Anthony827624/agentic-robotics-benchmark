# Build log of the reference unit (colleague's notes)

[BL-01] Printing. All ten printed parts in PLA+, 0.2 mm layers, 3 perimeters, 15 % gyroid infill, 4 top / 4 bottom
layers; same printer and filament batch for every part.

[BL-02] Weight. The ten printed parts weighed 0.300 kg together before assembly (kitchen scale, +-2 g). Servos, screws and
cables not included. I did not weigh the parts one by one.

[BL-03] Harness. Measured 0.3 V drop between the supply terminals and the shoulder servo's connector during a fast move;
assume the same at every servo.

[BL-04] Cameras. Photos from my phone: the main camera (a wide lens class) and the 2x camera (tele); the class of every photo
is in photos/photos.json. PH-01..PH-12 are from three tripod stations T1 (front-left), T2 (right side) and T3 (high,
front-right) (PH-01 / 04 / 07 / 10 from T1, PH-02 / 05 / 08 / 11 from T2, PH-03 / 06 / 09 / 12 from T3); the tripods stood
still while I moved the arm through four poses P1..P4 (PH-01..03 = P1, 04..06 = P2, 07..09 = P3, 10..12 = P4), and the arm
stood still while the three photos of a pose were taken. The phone refocuses for every photo. Everything else is hand-held.

[BL-05] Stops. PH-13..PH-22: each arm joint driven slowly into its programmed angle limit (the mechanical stop of the
design), lower and upper, with the other joints at whatever pose they were in; photos.json names the joint and the stop.
The jaw's open stop is in PH-23 and PH-25, closed in PH-24; PH-23..PH-25 show the arm in one and the same pose (only the
jaw moved for PH-24), PH-23 from the front and PH-25 from the side. PH-26..PH-28: the servo pockets (shoulder and elbow;
wrist roll; jaw). Servo fit as I assembled it: the base, shoulder, elbow and wrist-pitch servos' cases sit directly against their pocket walls; the wrist-roll servo sits between three blocks printed in one piece with the wrist part, which fill the pocket around its case; the jaw servo's case sits directly against the pocket walls of the gripper part.

[BL-06] Things of known size. The arm stands directly on the oak bench top (no mat); the bench top is the mounting plane.
I laid a 300 mm steel rule (1 / 5 / 10 mm ticks) next to the arm for PH-03 and PH-29 only, an ID-1 card
(85.60 x 53.98 mm) for PH-23 only, and stood a beverage can (66 mm diameter, 122 mm tall) on the bench
for PH-05 and PH-30 only; the kitchen scale with a 150 mm steel rule in front of it is only in PH-31. The servo cases
are the vendor's (outline drawings on the datasheet pages).

[BL-07] Orientation. The white arrow sticker on the bench in front of the base marks yaw 0 (the arm's forward direction).

[BL-08] Servos and lots. The unit ran on its original servos (to be replaced, B-01); the new servos go into the same
pockets. Kestrel Motion incoming-inspection reports in lot_reports/: LR-2026-0650 (lot KM-2026-W27) and LR-2025-1143 (lot KM-2025-W45). Altair Drives
parts were never inspected here.

[BL-09] Objects. PH-30: the customer's objects next to the can; PH-31: the heaviest of them on our kitchen scale (1 g
display resolution, tared) with the 150 mm rule in front of it.

[BL-10] Repair (2026-05-08). The wrist part cracked at a screw boss during the endurance runs; before the photo session I reprinted it in PETG from another spool (4 perimeters, 45 % gyroid infill, otherwise as BL-01): it is the darker, glossier part in the photos. The new part weighed 1.39 x the original one on the lab scale. BL-02 is the weight of the original ten parts.

[BL-11] Forearm revision. The CAD drawing of the arm (rev A, 2025-11-03) dimensions the forearm at 148.0 mm between the elbow and wrist-pitch axes. On 2026-02-14 I printed and fitted a longer forearm (rev B, for the reach an earlier customer asked for); the drawing was never updated. The unit in the photos has the rev B forearm, and the weight in BL-02 is with it.
