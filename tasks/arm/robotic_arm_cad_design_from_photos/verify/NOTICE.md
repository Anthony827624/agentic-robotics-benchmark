# NOTICE - source of the ground truth of this task (verify / -dev only; never shown to the agent)

The photographed unit `H_hdg` of this task (its ground truth in `reference/`, the oracle document in
`../oracle/reference/`) is a **modified derivative** of the open-hardware **SO-ARM101 follower arm** published by
**TheRobotStudio** (with Hugging Face / LeRobot):

- repository: https://github.com/TheRobotStudio/SO-ARM100
- commit: `5f6d2b876a53a4872e405b991dd925556c9e38a4` (checkout on the authoring host, recorded by the tier generator
  `docs/plan/arm/chain/shared/arm/gt_arm/source.py`)
- licence: Apache License 2.0 (the repository's `LICENSE`); this notice is the attribution the licence asks for, and the
  statement of modifications of its section 4(b): the ten printed parts were re-exported as B-rep solids in world-aligned
  link frames at a straight-up zero pose, then perturbed by the information tier's generator `gt_arm` (family
  `arm_family_v1`, generator `gt-arm/1.2.0`): band stretches of the base, shoulder, upper arm, forearm, wrist and
  fingers, a shoulder offset change, printed spacer blocks in servo pockets, and cut-outs (lightening windows, cable
  slots, a notch), re-levelled so that the zero pose stays straight up; they were recoloured and photographed with the
  render layer (Blender Cycles backend). The derived material is distributed under the same licence terms. The task
  never names the source.
- the bought servo is the tier's neutral `BUS-SERVO-12V-A`: a stand-in generated from datasheet numbers
  (`shared/arm/cad_drawings/servo_sts3215.py` as re-hosted by `gt_arm.servo_body`); no vendor geometry is shipped.
- the photo scenes use procedural, self-made assets (bench, cutting mat, checker targets, rulers, ID-1 card, paper tags;
  `shared/render/scale_refs.py`, `textures.py`) inside the lab room `room_lab_v4` of the project's licensed Unity asset
  pack (Archviz Laboratory Pack); the Cycles world light is the outdoor HDRI
  `schadowplatz_4k.hdr` of the project's licensed Unity asset pack (`docs/plan/arm/DRIVE_ASSETS.md`), which is not shipped
  with the task (only the rendered photos are).
- the public-model digest list (`public_blocklist.json`) holds sha256 digests of the source repository's CAD / mesh files and
  of the MuJoCo Menagerie model of the same arm (`robotstudio_so101` @ `c96a32d`, Apache-2.0), plus vertex fingerprints of the
  nominal member's re-exported parts; it contains no geometry.
- FreeCAD 1.1.4 (LGPL-2.0-or-later, https://github.com/FreeCAD/FreeCAD) is installed unmodified in the task image from its
  release AppImage, with the FreeCAD MCP server neka-nat/freecad-mcp @ `78bf023` (MIT, https://github.com/neka-nat/freecad-mcp)
  vendored unmodified (`image/vendor/freecad-mcp/`).
