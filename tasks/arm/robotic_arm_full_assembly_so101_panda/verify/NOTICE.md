# NOTICE (grader-side; never staged into the agent's home)

The arm of this task is a hidden variant (`H_asm2`, task version 2; information-tier generator `shared/arm/gt_arm/`, family `arm_family_v1`,
generator gt-arm/1.1.0) of the SO-ARM101 follower arm by TheRobotStudio / Hugging Face LeRobot
(https://github.com/TheRobotStudio/SO-ARM100, commit 5f6d2b876a53a4872e405b991dd925556c9e38a4, Apache-2.0): its printed
parts are B-rep stretches, pocket spacers and cut-outs of the repository's STEP parts, re-levelled in the tier frame, with
the servo zero offsets of the C layer. The servo is modelled on the Feetech STS3215 as the tier servo `BUS-SERVO-12V-A`
(generated from datasheet numbers, `shared/arm/cad_drawings/servo_sts3215.py`). CITATION: Knight, Kooijmans, Cadene et
al., "Standard Open SO-100 & SO-101 Arms", 2024.

The task's scene (a workbench with the arm's base on a fixture and every other part loose on the bench, a Panda that
builds the arm) started as a MuJoCo re-creation from numbers of the `assembly.so101` task of EmbodiedSWE-Bench
(https://embodiedswe.github.io/, repository https://github.com/EmbodiedSWE/EmbodiedSWE, commit d34837e99e5016525f0e9b6bb84791eb3d4b8162,
Apache-2.0) and was extended to the whole arm; no EmbodiedSWE dataset asset is used. The Franka Panda is the MuJoCo
Menagerie `franka_emika_panda` model (Apache-2.0, commit c96a32d). The agent cameras' RGB is rendered by MuJoCo's Filament module
(`arm/assembly_full/agent_render.py`; the look and the room loader vendored from the chain's `render_filament/` and
`room/`) in a factory hall built from the FactoryWarehouse Interior Modular Pack 1.0.1 (CyberneticWalrus) and the wall
row of the Archviz Laboratory Pack 2.0 (Unity Asset Store; licensed for redistribution), lit by a Blender Cycles bake;
the bench top is Poly Haven's oak_veneer_01 (CC0). Depth, the photo pipeline and the depth-sensor model are the chain's
render layer `shared/render/` (MuJoCo backend, OSMesa). The collision pieces the grader carves (`arm/assembly_full/
fine_collision.py`) use CoACD (MIT) and manifold3d (Apache-2.0) from the image.

Per the owner's direction (2026-09-27) the givens, the instruction and the public card never name the arm, the
benchmark, the model collection or the servo vendor; this file and the -dev folder carry the attribution.
