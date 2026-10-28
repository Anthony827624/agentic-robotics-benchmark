# Hidden scenes

One folder per clip: `motion.pt` (robot start state, object and hand references in the simulator
frame), the object's mesh (`visual.obj`), its convex collision parts (`convex/`) and its Isaac Sim
conversion (`.isaac_usd/`); `scene_manifest.json` records how each scene was sliced from the
source pack and its hashes. Signed-distance grids are rebuilt on first use into `.sdf_cache/`
(deterministic, a few seconds per object).

Derived from the HOT3D dataset (Meta Platforms Technologies, LLC; Banerjee et al., "HOT3D: Hand
and Object Tracking in 3D from Egocentric Multi-View Videos", arXiv:2406.09598) and adapted:
poses moved into the simulator world frame, hand references retargeted to the Sharpa hand,
meshes convex-decomposed and converted for Isaac Sim. The files keep HOT3D's terms
(`LICENSE-HOT3D.txt`): sequence and object data CC BY-SA 4.0 (object models may not be sold),
hand data and its derivatives CC BY-NC-SA 4.0, non-commercial. A file mixing several kinds, such
as `motion.pt`, carries the most restrictive of them. The MANO model is not included.
