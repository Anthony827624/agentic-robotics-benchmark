"""The full-arm assembly cell behind the camera-only API (`FullArmInfoServer`).

The physics is `cell.FullArmCell`: only the base mounted on the fixture, the 15 other parts (six bus servos, three
brackets, six printed links) resting on the bench where the Panda reaches them, the coupling state machine
(`coupling.py`: a part on its feature is held by a soft coupling, a fastened part is welded, a fastened joint stays free
against the unpowered servo's gearbox friction within the arm's joint limits). The observation side is the information
tier's (`arm.assembly_info.cell_info.InfoCellServer`): uncalibrated RGB-D cameras through the render layer, a noisy
flange F/T sensor, the joint state; no part poses, no mate events.

Per episode the bench layout is drawn from the episode seed (`cell.jitter_layout`): every part in a stable resting
orientation of the grader's bench plan, turned and moved by a random amount. With the oracle token (privileged mode)
and `oracle_nominal_layout` the layout is the plan itself (the reference assembler is tuned on it), and the privileged
call `reference_assemble()` runs that assembler (`reference.program`) inside the cell: it acts on the same physics
through the same control ticks and safety watch as any program.

Score of an episode (`finalize`): `stage_score` = the achieved mates' weight / the scope's total weight (18.0): a seat
or a bracket counts 1.0, a horn mate 1.5, counted when the cell has fastened it (and, for a horn mate, within 3 deg of
the design rotation).
"""

import json
import math
import os
import time

import numpy as np

from ..assembly_cell.server import _deep_update
from ..assembly_info import layout_info as LI
from ..assembly_info.cell_info import DEFAULT_INFO_CONFIG, InfoCellServer, cell_datums
from .cell import ORDER, FullArmCell, jitter_layout

CELL_VERSION = "arm-full-assembly-cell/1.0.0"
PUBLIC_METHODS = ("describe", "get_state", "set_control", "command", "step", "get_camera", "finish")
PRIVILEGED_METHODS = ("get_state_privileged", "describe_privileged", "reference_assemble")

_MOUNT_TEXT = {
    "top": "overhead, roughly 1.25 m above the bench near its middle, looking straight down; the image covers the whole working area "
           "(every part, the fixture, the robot base, the bench board); image width roughly along the cell's x",
    "wrist": "on the Panda hand's +x flank, a few centimetres off the flange axis, tilted towards the fingertips",
}

DEFAULT_FULL_CONFIG = json.loads(json.dumps(DEFAULT_INFO_CONFIG))
DEFAULT_FULL_CONFIG.update({
    "sim_cap_s": 1200.0,
    "wall_cap_s": 2400.0,
    "silence_s": 120.0,
    "camera_budget_frames": 600,
    # the episode's bench layout around the bench plan
    "bench_jitter": {"xy_m": 0.03, "yaw_deg": 30.0, "gap_m": 0.006},
    "fixture_jitter_m": 0.01,
    "oracle_nominal_layout": True,
    "privileged_wall_cap_s": 7200.0,
    # the agent's cameras: "filament" (agent_render.py: physically based, the baked factory hall) or "mujoco"
    # (assembly_info.cell_render: MuJoCo's classic renderer)
    "agent_renderer": "filament",
})


class FullArmInfoServer(InfoCellServer):
    """`kit_dir` = the GT kit (verify/gt), `bench_plan` = {part: {R, xy, z, grasp}} (hidden), `cache_dir` = the cell's
    collision-piece cache (the carved / finer convex pieces, deterministic per kit)."""

    def __init__(self, kit_dir, panda_dir, bench_plan, cache_dir, config=None, mode="hidden", privileged=False, render=True, log=None,
                 kit_info=None):
        cfg = _deep_update(json.loads(json.dumps(DEFAULT_FULL_CONFIG)), config or {})
        super().__init__(kit_dir, panda_dir, config=cfg, scope_ids=None, mode=mode, privileged=privileged, render=render, log=log,
                         kit_info=kit_info)
        self.cfg = cfg
        self.scope_ids = list(self.graph["scopes"]["default"])
        self.bench_plan = bench_plan
        self.camera_names = tuple(self.cfg.get("agent_cameras") or ("top", "wrist"))
        self.cache_dir = cache_dir
        self.fa = None

    # ------------------------------------------------------------------ episode setup
    def reset(self, seed):
        # the agent's cameras: `agent_cameras` (top + wrist, both RGB-D); the information tier's other mounts are not drawn
        self.episode = LI.draw_episode(seed, self.cfg.get("info_layout"), strict=False)
        missing = [n for n in self.camera_names if self.episode["cameras"].get(n) is None]
        if missing and self.mode == "hidden" and self.render_enabled:
            raise LI.MissingMount("camera(s) %s have no mount in the cell's config" % ", ".join(missing))
        nominal = bool(self.privileged and self.cfg.get("oracle_nominal_layout", True))
        bj = self.cfg["bench_jitter"]
        if nominal:
            staging = jitter_layout(self.parts, self.bench_plan, seed, xy_m=0.0, yaw_rad=0.0, gap_m=0.0, tries=1)
        else:
            staging = jitter_layout(self.parts, self.bench_plan, seed, xy_m=float(bj["xy_m"]), yaw_rad=math.radians(float(bj["yaw_deg"])),
                                    gap_m=float(bj["gap_m"]))
        from .reference import READY
        fj = 0.0 if nominal else float(self.cfg.get("fixture_jitter_m", 0.01))
        rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, (int(seed) >> 32) & 0xFFFFFFFF, 0xF1C7])
        fixture_xy = (0.10 + float(rng.uniform(-fj, fj)), float(rng.uniform(-fj, fj)))
        t0 = time.time()
        fa = FullArmCell(self.kit_dir, self.panda_dir, self.cache_dir, panda_q=READY, log=self.log, staging=staging, use_cache=False,
                         fixture_xy=fixture_xy, printed_rgba=(self.kit_info or {}).get("rgba"))
        fa.recording = False
        self.log("[cell] full-arm cell for seed %d built and settled in %.0f s" % (int(seed) & 0xFFFFFFFF, time.time() - t0))
        self.fa = fa
        self.build = fa.build
        self.model, self.meta, self.data = fa.m, fa.meta, fa.d
        self.meta["joint_axis_in_child"] = self.joint_axis_in_child
        self.layout = {"parts": {n: {"pos": p, "quat": q} for n, (p, q) in staging.items()}, "nominal": nominal,
                       "base_yaw_rad": 0.0, "fixture": {"pos": [fixture_xy[0], fixture_xy[1], 0.0]}}
        self.preassembly = fa.srv.preassembly
        self._init_state(seed)
        self._close_session()
        self.frames_used = 0
        self.frames_by_camera = {n: 0 for n in self.camera_names}
        self.render_wall_s = 0.0
        self.ep_seed = int(seed)
        hi = (int(seed) >> 32) & 0xFFFFFFFF
        self._wrench_rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, 0x3E17C4] + ([hi] if hi else []))
        # every control tick goes through the cell's watch, whoever drives it (an agent's step() or the reference)
        raw = fa.tick_one
        self._raw_tick = raw

        def routed():
            self.physics_tick(track=True)
        fa.tick_one = routed
        return {"ok": True}

    def _init_state(self, seed):
        """The information tier's per-episode state (the six-servo watch's bookkeeping) on the full-arm cell's data."""
        m = self.model
        self.seed = int(seed)
        self.done = False
        self.done_reason = None
        self.tick_count = 0
        self.events_log = []
        self.violations = []
        self.illegal_calls = 0
        self.calls = {}
        self.step_calls = 0
        self.regrasps = 0
        self.held = None
        self._held_cand = None
        self._held_ticks = 0
        self._drop_watch = {}
        self._lost = set()
        self._dropped = set()
        self.wall_start = None
        self.wall_used = 0.0
        self.renderer = None
        self.tick_s = self.fa.tick_s
        self.tick_steps = self.fa.tick_steps
        self.ctrl = self.fa.ctrl
        self.tracker = self.fa.tk
        self.sm = self.fa.sm
        self._obs_noise = (0.0, 0.0)
        self._obs_rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, 0x0B5E])
        self.geom_class = np.zeros(m.ngeom, dtype=int)
        self.geom_part = {}
        panda_bodies = set(b for b in range(m.nbody) if m.body(b).name.startswith("panda/"))
        loose_bodies = {m.body(n).id: n for n in self.meta["loose"]}
        for s in self.meta["servos"]:
            loose_bodies[m.body("horn_" + s).id] = s
        self.loose_body_ids = loose_bodies
        self.static_env_bodies = set([0, m.body("fixture").id, m.body(self.meta["root"]).id])
        pads = set(self.meta["pad_geoms"])
        for g in range(m.ngeom):
            b = m.geom_bodyid[g]
            name = m.geom(g).name
            if name in pads:
                self.geom_class[g] = 2 if "left" in name else 3
            elif b in panda_bodies:
                self.geom_class[g] = 1
            elif b in loose_bodies:
                self.geom_class[g] = 4
                self.geom_part[g] = loose_bodies[b]
            elif b in self.static_env_bodies:
                self.geom_class[g] = 5
        self.part_body = {n: m.body(n).id for n in self.meta["loose"]}
        self.part_qadr = {n: m.jnt_qposadr[m.joint("free_" + n).id] for n in self.meta["loose"]}
        self.part_vadr = {n: m.jnt_dofadr[m.joint("free_" + n).id] for n in self.meta["loose"]}
        self.sensor_force = m.sensor("hand_force").id
        self.sensor_torque = m.sensor("hand_torque").id
        self._f6 = np.zeros(6)
        self._dmg_peak = {"pad": 0.0, "part": 0.0}
        self._dmg_over = {"pad": 0, "part": 0}
        self._dmg_sustained = {"part": 0.0}
        self._part_hist = []

    # ------------------------------------------------------------------ physics
    def physics_tick(self, track=True):
        if self.done:
            return []
        n0 = len(self.sm.events)
        self._raw_tick()
        self.tick_count += 1
        events = []
        if track:
            events += ["%s" % e for _t, e in self.sm.events[n0:]]
            events += self._watch(self.data)
            for e in events:
                self.events_log.append((float(self.data.time), e))
        if self.data.time >= self.cfg["sim_cap_s"] - 1e-9:
            self._end("sim_cap")
        return events

    # ------------------------------------------------------------------ the restricted API
    def describe(self):
        out = super().describe()
        out["cell_version"] = CELL_VERSION
        out["cameras"] = {n: v for n, v in out["cameras"].items() if n in self.camera_names}
        out["task"] = ("assemble the whole arm from the base: the base is clamped on the fixture; seat the six servos, clamp the "
                       "three brackets and mount the six links on their horns in the order of the picture manual")
        for n in out["cameras"]:
            out["cameras"][n]["mount"] = _MOUNT_TEXT.get(n, "")
        out["items"] = ["the arm's base, clamped on the fixture at about (0.10, 0.00) m",
                        "the six bus servos, the three brackets and the six printed links of the arm, each resting on the bench in "
                        "a stable orientation within the robot's reach (their places and orientations vary between episodes)"]
        out["trays"] = "none"
        out["robot"]["home_q"] = list(self.ctrl.q_cmd)
        return out

    def _fastening_text(self):
        out = super()._fastening_text()
        out["coupling"] = ("a part that is ON its feature is held there by the cell (a servo or bracket in its pocket within 4 mm across "
                           "and at most 3 mm short of home; a fork on its horn within 4 mm of the horn axis, 8 deg of its direction "
                           "and 4 mm axially): it stays on when it is let go and can then fasten; a fastened joint of the arm stays "
                           "free: the servos are unpowered during the assembly and back-drive against their gearbox friction "
                           "(about 0.4 N m) within the arm's joint limits")
        return out

    def _session(self):
        if self.session is None:
            from ..assembly_info import cell_render
            from . import agent_render
            t = time.time()
            want = str(self.cfg.get("agent_renderer", "filament"))
            ok, why = agent_render.available() if want == "filament" else (False, "configured")
            if want == "filament" and not ok:
                raise RuntimeError("agent renderer filament unavailable: %s" % why)
            self.session = agent_render.open_session(self) if ok else cell_render.open_session(self)
            self.renderer = "filament" if ok else "mujoco"
            self.render_wall_s += time.time() - t
            self.log("[cell] %s render session opened in %.1f s" % (self.renderer, time.time() - t))
        return self.session

    def photo_spec(self, name):
        """The information tier's photo pipeline with the colour jitter drawn once per camera unit and episode: a live
        camera's gain / tint does not change from frame to frame (drawn per frame, the agent's frames flickered by about
        4 % in brightness); the sensor noise stays per frame."""
        import dataclasses
        from render.spec import derive_seed
        p = super().photo_spec(name)
        return dataclasses.replace(p, colour_jitter=dataclasses.replace(p.colour_jitter, seed=derive_seed(self.ep_seed, "colour_unit", name)))

    def get_camera(self, name="top", depth=True):
        if str(name) not in self.camera_names:
            return {"error": "unknown camera (cameras: %s)" % ", ".join(self.camera_names)}
        return super().get_camera(name, depth=depth)

    def describe_privileged(self):
        out = {"layout": self.layout, "mates": [dict(g) for g in self.graph["mates"] if g["id"] in set(self.scope_ids)],
               "states": dict(self.sm.state), "score": self.sm.score()}
        return json.loads(json.dumps(out, default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)))

    def get_state_privileged(self):
        d = self.data
        parts = {n: {"pos": [float(v) for v in d.xpos[b]], "quat": [float(v) for v in d.xquat[b]]} for n, b in self.part_body.items()}
        return {"sim_time": float(d.time), "parts": parts, "states": dict(self.sm.state), "score": self.sm.score()}

    def reference_assemble(self):
        """The privileged reference assembler on this episode (oracle token only)."""
        from .reference import program
        report = program(self.fa, rest=self.bench_plan, final_checks=False, log=self.log)
        return {"ok": True, "score": float(self.sm.score()), "placed_by_panda": int(report.get("placed_by_panda", 0)),
                "unplanned_contacts": int(report.get("unplanned_contacts_total", 0))}

    def handle(self, method, params):
        if method in PRIVILEGED_METHODS:
            self.calls[method] = self.calls.get(method, 0) + 1
            if not self.privileged:
                self.illegal_calls += 1
                return {"error": "forbidden"}
            return getattr(self, method)()
        return super().handle(method, params)

    # ------------------------------------------------------------------ end of episode
    def finalize(self, functional_rng=None, run_functional_check=False):
        self._close_session()
        if not self.done:
            self._end("solve_returned")
        rec = self.tracker.record()
        weights = {m["id"]: m["weight"] for m in self.tracker.mates}
        W = sum(weights.values())
        achieved_w = sum(weights[r["id"]] for r in rec if r["achieved"])
        hj = {s: float(self.data.qpos[self.model.jnt_qposadr[self.model.joint("hj_" + s).id]]) for s in self.meta["servos"]}
        return {
            "cell_version": CELL_VERSION, "seed": self.seed, "scope": list(self.scope_ids), "done_reason": self.done_reason,
            "sim_time_s": float(self.data.time), "wall_time_s": float(self.wall_used), "ticks": int(self.tick_count),
            "mates": rec, "states": dict(self.sm.state), "weights": weights, "stage_score": achieved_w / W if W else 0.0,
            "n_achieved": int(sum(1 for r in rec if r["achieved"])), "n_fastened": int(sum(1 for r in rec if r.get("fastened"))),
            "all_mates": bool(all(r["achieved"] for r in rec)),
            "violations": list(self.violations), "safety_ok": len(self.violations) == 0,
            "illegal_call_count": int(self.illegal_calls), "calls": dict(self.calls), "step_calls": int(self.step_calls),
            "lost_parts": sorted(self._lost), "dropped_parts": sorted(self._dropped),
            "events": [[t, e] for t, e in self.events_log][-400:], "coupling_events": [list(e) for e in self.sm.events],
            "hinge_angles_rad": hj, "layout": self.layout, "damage": self.damage_summary(),
            "camera_frames": dict(self.frames_by_camera), "camera_frames_total": int(self.frames_used),
            "render_wall_s": float(self.render_wall_s), "renderer": getattr(self, "renderer", None), "privileged_mode": bool(self.privileged),
            "lowest_part_z_m": float(self.fa.lowest_point()),
        }


__all__ = ["FullArmInfoServer", "CELL_VERSION", "DEFAULT_FULL_CONFIG", "ORDER", "cell_datums"]
