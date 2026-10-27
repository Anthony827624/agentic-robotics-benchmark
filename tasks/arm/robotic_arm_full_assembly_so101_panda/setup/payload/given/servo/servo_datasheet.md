# Bus servo BUS-SERVO-12V-A: datasheet summary

Pages: `DS-BUS-SERVO-12V-A.png` (datasheet), `DWG-BUS-SERVO-12V-A.png` (dimensioned outline). Nominal values.

| item | value |
|---|---|
| case | 45.4 x 24.8 x 28.8 mm between two 1.5 mm flanges; 34.6 mm overall (bottom flange face to horn face) |
| output | horn axis 12.5 mm from the case centre (10.2 mm from the front end of the case); horn disc 20 mm, four 2.5 mm holes on a 14.0 mm pitch circle (square pattern); a passive rear disc 20 mm coaxial on the opposite face |
| flanges | four 1.5 mm pilot holes per flange (M2 self-tapping) |
| connectors | 3-pin bus connectors under the rear flange at the case's back end (daisy chain) |
| mass | 60 g |
| stall torque | 2.94 N m at 12.0 V (typical, momentary) |
| no-load speed | 6.49 rad/s at 12.0 V |
| supply | 9.0-12.6 V |
| encoder | magnetic, 4096 ticks per turn; bus reading b = (ticks - 2048) x 2 pi / 4096 rad; zero offset programmable |
| variants | with horn (fitted on the output spline) / horn-less (output spline only) |

**CAD frame of `parts/servo.stl` / `parts/servo_hornless.stl`** (millimetres): +z = the horn axis, pointing out of the horn
face; the horn axis passes through the origin; z = 0 is the bottom face of the case (the horn-side flange is the top
flange, the rear disc is below z = 0); the case extends along -x (its centre at x = -12.5), the connectors at its -x end.
