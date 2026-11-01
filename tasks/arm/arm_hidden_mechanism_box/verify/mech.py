"""Hidden-mechanism box builder (task M `arm_hidden_mechanism_box`).

r5 (2026-10-22): window rule, sector rims and gap == credit window adopted from the mech-qa r2 review
(docs/reviews/M_mechqa_r2.md, validated prototype fixproto3); friction-held back-drive check added.

Builds the MJCF fragment of one puzzle box from a config dict and checks, in MuJoCo, that every
declared interlock really blocks and really releases.  Interlocks are pure geometry: a hidden
"finger" on the blocked element runs into a hidden "bar" on the blocking element until the blocking
element is moved out of the way.  Each interlock pair uses its own collision bit, so hidden geoms
only ever touch their partner.  Parts keep their position through joint friction (no springs, no
state machine).

Box frame: origin at the centre of the box bottom; +z up; the FRONT face is the -x face (it faces
the robot at yaw 0); LEFT = +y face, RIGHT = -y face.  The lid is hinged along the top back edge.
"""

import json
import math

import numpy as np

T = 0.012                 # wall thickness
GAP = 0.0025              # free play of a blocked element before its finger touches the bar
F_HALF = (0.005, 0.003, 0.010)   # finger box half sizes along (motion of B, release motion of A, third)
B_HALF = (0.005, 0.003, 0.010)   # bar box half sizes
KNOB_R = 0.016            # knob radius
KNOB_ARM = 0.016          # radius of the knob's hidden arm
RING_R = 0.022            # radius of a knob's hidden notched ring
KW_HALF0 = 0.003          # slab / finger half thickness of a slider window that stops a knob
KNOB_STOP_R = 0.035       # radius at which a slider/button stops a knob (short lever arm, rev-redteam F1)
LATCH_IN = 0.006          # inward travel of a latched bolt pin
LATCH_CLEAR = 0.004       # inward travel at which a latched bolt is free to slide
LATCH_K = 600.0           # return spring of a latched bolt pin (N/m)
FW_HALF = (0.005, 0.001, 0.010)  # finger of a window pair / latch: thin across the release motion
LID_RANGE = math.radians(110.0)
FACES = ("front", "left", "right")
TYPES = ("button", "bolt", "knob", "panel")
FINALS = ("lid", "drawer")

# bit 0: robot-touchable parts; bit 1: reserved; bits 2..: interlock pairs
TOUCH = 'contype="1" conaffinity="2" group="1"'
VISUAL = 'contype="0" conaffinity="0" group="1"'


def _f(v):
    return " ".join("{:.6g}".format(float(x)) for x in np.ravel(v))


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def _quat_from_axes(e1, e2):
    """Quaternion of the frame whose x axis is e1 and y axis is (e2 made orthogonal to e1)."""
    e1 = _unit(e1)
    e2 = np.asarray(e2, float) - np.dot(e2, e1) * e1
    e2 = _unit(e2)
    e3 = np.cross(e1, e2)
    R = np.stack([e1, e2, e3], axis=1)
    import mujoco
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, R.reshape(9))
    return q


def _quat_z_to(n):
    """Quaternion rotating +z onto n (for cylinders whose axis must lie along n)."""
    n = _unit(n)
    z = np.array([0.0, 0.0, 1.0])
    c = float(np.dot(z, n))
    if c > 1 - 1e-9:
        return np.array([1.0, 0, 0, 0])
    if c < -1 + 1e-9:
        return np.array([0.0, 1.0, 0, 0])
    ax = _unit(np.cross(z, n))
    a = math.acos(c)
    return np.concatenate([[math.cos(a / 2)], math.sin(a / 2) * ax])


def face_frame(shell, face):
    """(outward normal n, inward k, function (u, v) -> mount point) of a face."""
    D, W = shell["D"], shell["W"]
    if face == "front":
        n = np.array([-1.0, 0, 0])
        return n, -n, lambda u, v: np.array([-D / 2, u, v])
    if face == "left":
        n = np.array([0, 1.0, 0])
        return n, -n, lambda u, v: np.array([u, W / 2, v])
    if face == "right":
        n = np.array([0, -1.0, 0])
        return n, -n, lambda u, v: np.array([u, -W / 2, v])
    raise ValueError(face)


class Box(object):
    """Geometry of one configured box; all positions in the box frame."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.shell = cfg["shell"]
        self.final = cfg.get("final", "lid")
        assert self.final in FINALS
        self.el = {}
        for e in cfg["elements"]:
            e = dict(e)
            assert e["type"] in TYPES, e
            n, k, mp = face_frame(self.shell, e["face"])
            e["n"], e["k"], e["mount"] = n, k, mp(e["u"], e["v"])
            if e["type"] == "bolt":
                assert e["face"] == "front"
                e["axis"] = np.array([0.0, float(e["dir"]), 0.0])
            elif e["type"] == "button":
                e["axis"] = k.copy()
            elif e["type"] == "panel":
                assert e["face"] == "front"
                e["axis"] = np.array([0.0, 0.0, 1.0]) if e["slide"] == "up" else np.array([0.0, float(e["dir"]), 0.0])
            elif e["type"] == "knob":
                assert e["face"] == "front"
                e["axis"] = k * float(e["sense"])     # rotation axis; positive angle = unlock
            self.el[e["id"]] = e
        H, D = self.shell["H"], self.shell["D"]
        self.hinge = np.array([D / 2, 0.0, H])
        if self.final == "drawer":
            dr = cfg["drawer"]
            self.drawer = dict(dr)
        self.locks = [tuple(p) for p in cfg["locks"]]
        self.pairs = []
        for i, (a, b) in enumerate(self.locks):
            self.pairs.append(self._pair_geometry(i, a, b))
        self.latches = {}
        for e in self.el.values():
            if e["type"] == "bolt" and e.get("latched"):
                self.latches[e["id"]] = self.latch_geometry(e, len(self.pairs) + len(self.latches))
        assert len(self.pairs) + len(self.latches) <= 28

    # ---------------------------------------------------------------- motion helpers
    def motion_dir(self, eid, p):
        """Unit velocity direction of point p when element eid moves in its unlock sense."""
        if eid == "final":
            if self.final == "lid":
                w = np.array([0.0, 1.0, 0.0])
                return _unit(np.cross(w, p - self.hinge))
            return np.array([-1.0, 0.0, 0.0])
        e = self.el[eid]
        if e["type"] == "knob":
            c = e["mount"]
            r = p - c
            r = r - np.dot(r, e["k"]) * e["k"]
            return _unit(np.cross(e["axis"], r))
        return _unit(e["axis"])

    def _pair_geometry(self, i, a, b):
        """Hidden blocker segments (on blocker a) and the finger (on blocked b) for pair i.

        Plain pair: one bar just ahead of the finger along b's motion; a's release motion moves it out
        of the finger's path. Window pair (a has `window` = [lo, hi] in joint units): the bar has a
        gap that lines up with the finger only while a is inside its window (sliders: two segments
        along a's motion; knobs: a notched ring, only for b moving along the knob axis)."""
        A = self.el[a]
        if A.get("window") is not None and A["type"] != "knob":
            # mech-qa r2 D1 rule: a window blocker moves parallel to the rotation axis of a rotating
            # dependent, or perpendicular to a sliding dependent's motion; otherwise the dependent's
            # finger reaches the gap's end faces sideways and back-drives the blocker into its window
            ax_a = _unit(A["axis"])
            if b == "final" and self.final == "lid":
                rot_ax = np.array([0.0, 1.0, 0.0])
            elif b != "final" and self.el[b]["type"] == "knob":
                rot_ax = _unit(self.el[b]["axis"])
            else:
                rot_ax = None
            if rot_ax is not None and abs(float(np.dot(ax_a, rot_ax))) < 0.99:
                raise ValueError("pair {}->{}: window blocker must move along the dependent's rotation axis".format(a, b))
            if rot_ax is None:
                vb_lin = self.motion_dir(b, A["mount"])
                if abs(float(np.dot(ax_a, vb_lin))) > 0.1:
                    raise ValueError("pair {}->{}: window blocker must move across the dependent's motion".format(a, b))
        d = F_HALF[0] + GAP + B_HALF[0]
        depth = A.get("depth", 0.022)                   # bar sits this far behind A's face
        win = A.get("window")
        bit = 1 << (2 + i)
        if A["type"] == "knob" and win is not None:
            c = A["mount"] + A["k"] * 0.030
            vb0 = self.motion_dir(b, c)
            if abs(np.dot(vb0, A["k"])) < 0.9:
                raise ValueError("pair {}->{}: a notched knob only blocks motion along its axis".format(a, b))
            ref = np.asarray(A.get("ring_dir", [0.0, 0.0, -1.0]), float)
            w = _unit(ref - np.dot(ref, A["k"]) * A["k"])
            P = c + RING_R * w
            vb = self.motion_dir(b, P)
            ring_c = c + vb * d                          # ring plane centre (on the axis)
            ax = _unit(A["axis"])
            eu = np.cross(ax, w)
            lo, hi = win
            th_c = 0.5 * (lo + hi)
            # the finger's tangential half width subtends its largest angle at the ring's inner edge; the
            # segments next to the gap are short so their flat end faces hardly intrude into it
            r_in = RING_R - 0.006
            gap_half = 0.5 * (hi - lo) + math.atan(FW_HALF[1] / r_in)
            segs = []
            g_lo, g_hi = -th_c - gap_half, -th_c + gap_half
            spans = []
            short = math.radians(1.0)
            n_short = 3
            for a0, a1 in ((-math.pi / 2 - 0.25, g_lo - n_short * short), (g_hi + n_short * short, 0.25)):
                n_seg = max(1, int(math.ceil((a1 - a0) / math.radians(5.0))))
                step = (a1 - a0) / n_seg
                for k in range(n_seg):
                    spans.append((a0 + k * step, a0 + (k + 1) * step))
            for k in range(n_short):
                spans += [(g_lo - (k + 1) * short, g_lo - k * short), (g_hi + k * short, g_hi + (k + 1) * short)]
            ov_full = 0.0015 / RING_R                             # overlap neighbours, never into the gap
            for a0, a1 in spans:
                ov = min(ov_full, 0.5 * (a1 - a0))
                is_short = (a1 - a0) < 1.5 * short
                if a1 <= g_lo + 1e-9:
                    lo_e, hi_e = a0 - ov, min(a1 + ov, g_lo if is_short else g_lo - (n_short - 0.5) * short)
                else:
                    lo_e, hi_e = max(a0 - ov, g_hi if is_short else g_hi + (n_short - 0.5) * short), a1 + ov
                mid = 0.5 * (lo_e + hi_e)
                radial = math.cos(mid) * w + math.sin(mid) * eu
                tang = -math.sin(mid) * w + math.cos(mid) * eu
                half_t = RING_R * math.tan(0.5 * (hi_e - lo_e))
                segs.append((ring_c + RING_R * radial, _quat_from_axes(vb, tang), (B_HALF[0], half_t, 0.006)))
            fq = _quat_from_axes(vb, eu)
            return {"i": i, "a": a, "b": b, "segs": segs, "finger": P, "quat": fq, "bit": bit, "fhalf": FW_HALF}
        if A["type"] == "knob":
            c = A["mount"] + A["k"] * depth
            vb = self.motion_dir(b, c)
            w = vb - np.dot(vb, A["k"]) * A["k"]
            if np.linalg.norm(w) < 1e-6:
                w = np.array([0.0, 0.0, 1.0])
            w = -_unit(w)
            for _ in range(3):
                bar = c + KNOB_ARM * w
                vb = self.motion_dir(b, bar - d * vb)
                w2 = vb - np.dot(vb, A["k"]) * A["k"]
                if np.linalg.norm(w2) > 1e-6:
                    w = -_unit(w2)
            bar = c + KNOB_ARM * w
            rel = np.cross(A["axis"], w)                # initial motion direction of the bar
            if b != "final" and self.el[b]["type"] != "knob":
                # mech-qa r2 fix C (adopted from its validated prototype fixproto3): a sector of a ring about the knob axis instead of one flat bar.
                # The finger approaches the axis radially, so the rim's contact normal points through the
                # axis (no torque on the knob, no cam) and the blocked play stays 2.5 mm at every angle.
                ax = _unit(A["axis"])
                vb_ = self.motion_dir(b, bar)
                P = bar - d * vb_
                eu = np.cross(ax, w)
                th_end = math.radians(float(A.get("sector_deg", 50.0)))
                step = math.radians(3.0)
                spans, a0 = [], -th_end
                while a0 < 0.30 - 1e-9:
                    spans.append((a0, min(a0 + step, 0.30)))
                    a0 += step
                segs = []
                for s0, s1 in spans:
                    mid = 0.5 * (s0 + s1)
                    radial = math.cos(mid) * w + math.sin(mid) * eu
                    tang = -math.sin(mid) * w + math.cos(mid) * eu
                    half_t = (KNOB_ARM + B_HALF[0]) * math.tan(0.5 * (s1 - s0))      # outer corners meet: no steps
                    segs.append((c + KNOB_ARM * radial, _quat_from_axes(radial, tang), (B_HALF[0], half_t, B_HALF[2])))
                q = _quat_from_axes(vb_, rel - np.dot(rel, vb_) * vb_)
                return {"i": i, "a": a, "b": b, "segs": segs, "finger": P, "quat": q, "bit": bit,
                        "fhalf": (F_HALF[0], FW_HALF[1], F_HALF[2])}     # thin finger: contact stays on the axis line
        else:
            bar = A["mount"] + A["k"] * depth + np.asarray(A.get("bar_offset", [0, 0, 0]), float)
            if b == "final" and self.final == "lid":
                bar[2] = self.shell["H"] - 0.005          # level with the hinge: lid motion is vertical there
            rel = A["axis"]
            if b != "final" and self.el[b]["type"] == "knob":
                # meet the knob close to its axis (rev-redteam F1): a short lever arm keeps the stop stiff
                # under twisting; place the meeting point along the slider's motion so the knob's
                # tangential motion there is perpendicular to the release motion
                K = self.el[b]
                rp = rel - np.dot(rel, K["k"]) * K["k"]
                wdir = _unit(rp) if np.linalg.norm(rp) > 1e-6 else np.array([0.0, 0.0, 1.0])
                # finger exactly on the arc (radius KNOB_STOP_R, release motion radial), bar ahead along
                # the tangent: under torque the contact pushes the blocker towards its home stop
                if win is not None:
                    d = KW_HALF0 + GAP + KW_HALF0          # thinner slab and finger: short traversal
                P = K["mount"] + K["k"] * 0.03 + KNOB_STOP_R * wdir
                vb = self.motion_dir(b, P)
                bar = P + d * vb
        if b == "final" or self.el[b]["type"] != "knob" or A["type"] == "knob":
            vb = self.motion_dir(b, bar)
            for _ in range(3):
                P = bar - d * vb
                vb = self.motion_dir(b, P)
            P = bar - d * vb
        e2 = rel - np.dot(rel, vb) * vb
        if np.linalg.norm(e2) < 1e-6:
            raise ValueError("pair {}->{}: release motion parallel to blocked motion".format(a, b))
        if np.dot(_unit(rel), vb) < -0.15:
            raise ValueError("pair {}->{}: release motion closes the gap".format(a, b))
        q = _quat_from_axes(vb, e2)
        e2 = _unit(e2)
        if win is None:
            # wider overlap across the release motion (rev-redteam F1: no sideways slip under load); a
            # button blocker still clears it within its 10 mm travel
            h2 = 0.003 if A["type"] == "button" else 0.006
            if b != "final" and self.el[b]["type"] == "knob" and A["type"] != "knob":
                h2 = 0.003                                  # the knob stop pushes its blocker home, no slip
            return {"i": i, "a": a, "b": b, "segs": [(bar, q, (B_HALF[0], h2, B_HALF[2]))], "finger": P, "quat": q,
                    "bit": bit, "fhalf": (F_HALF[0], h2, F_HALF[2])}
        # slider window: two segments with a gap
        alpha = float(np.dot(_unit(rel), e2))
        lo, hi = win
        qc = 0.5 * (lo + hi)
        trav = A.get("travel", 0.010)
        c0 = -alpha * qc
        gh = FW_HALF[1] + alpha * 0.5 * (hi - lo)
        bh, fh = B_HALF, FW_HALF
        rot = None
        if b != "final" and self.el[b]["type"] == "knob":
            rot = (self.el[b]["mount"], _unit(self.el[b]["axis"]), math.pi / 2)
        elif b == "final" and self.final == "lid":
            rot = (self.hinge, np.array([0.0, 1.0, 0.0]), LID_RANGE)
        if rot is not None:
            # the finger crosses the slab on an arc: the gap follows its swept band (rev-redteam F1,
            # rev-quality R1: the functional window equals the credit window)
            fh = (KW_HALF0, FW_HALF[1], FW_HALF[2]) if b != "final" else FW_HALF
            bh = (KW_HALF0, B_HALF[1], B_HALF[2]) if b != "final" else B_HALF
            r0, r1 = self._arc_sweep(rot, P, vb, e2, bar, fh, bh[0])
            c0 += 0.5 * (r0 + r1)
            gh = 0.5 * (r1 - r0) + alpha * 0.5 * (hi - lo)   # mech-qa r2 D3: gap == credit window
        L = alpha * trav + 0.012
        segs = [(bar + e2 * (c0 - gh - L / 2), q, (bh[0], L / 2, bh[2])),
                (bar + e2 * (c0 + gh + L / 2), q, (bh[0], L / 2, bh[2]))]
        return {"i": i, "a": a, "b": b, "segs": segs, "finger": P, "quat": q, "bit": bit, "fhalf": fh}

    @staticmethod
    def _arc_sweep(rot, P, vb, e2, bar, fh, bh0):
        """Band [r0, r1] (along e2, relative to the bar centre) covered by the part of the finger that
        lies inside the bar slab (|vb offset| < bh0) at some angle of the blocked part's rotation
        rot = (centre, unit axis, range). Planar when the axis is along vb x e2; when the axis is along
        e2 the band is the finger's own width."""
        c, ax, rng = rot
        e3 = np.cross(vb, e2)
        pt = float(np.dot(P - bar, e2))
        if abs(np.dot(ax, e2)) > 0.99:
            return pt - fh[1], pt + fh[1]
        if abs(np.dot(ax, e3)) < 0.99:
            raise ValueError("finger arc is not planar in the bar frame")
        cs, ct = float(np.dot(c - bar, vb)), float(np.dot(c - bar, e2))
        ps = float(np.dot(P - bar, vb))
        sgn = float(np.dot(e3, ax))                       # +1: rotation from vb towards e2
        rect = [(ps + sx * fh[0], pt + sy * fh[1]) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]

        def clip(poly, lim, keep_below):
            out = []
            for k in range(len(poly)):
                a, b = poly[k], poly[(k + 1) % len(poly)]
                ia = (a[0] <= lim) if keep_below else (a[0] >= lim)
                ib = (b[0] <= lim) if keep_below else (b[0] >= lim)
                if ia:
                    out.append(a)
                if ia != ib:
                    f = (lim - a[0]) / (b[0] - a[0])
                    out.append((lim, a[1] + f * (b[1] - a[1])))
            return out

        r0, r1 = np.inf, -np.inf
        for th in np.linspace(0.0, rng, 1441):
            co, si = math.cos(th), sgn * math.sin(th)
            poly = [(cs + (x - cs) * co - (y - ct) * si, ct + (x - cs) * si + (y - ct) * co) for x, y in rect]
            poly = clip(clip(poly, bh0, True), -bh0, False)
            if poly:
                ts = [p[1] for p in poly]
                r0, r1 = min(r0, min(ts)), max(r1, max(ts))
        return r0, r1

    def latch_geometry(self, e, k_index):
        """Latched bolt: a finger on the bolt and a fixed stop on the case; the stop is cleared once
        the pin is pressed LATCH_CLEAR into the face."""
        e1 = _unit(e["axis"])
        e2 = e["k"]
        Lf = e["mount"] + e["k"] * 0.022 + np.array([0.0, 0.0, -0.022])
        lo_e2, hi_e2 = -0.02, LATCH_CLEAR - FW_HALF[1]
        lo_e1 = F_HALF[0] + GAP
        hi_e1 = lo_e1 + e["travel"] + 0.012
        Ls = Lf + e1 * (0.5 * (lo_e1 + hi_e1)) + e2 * (0.5 * (lo_e2 + hi_e2))
        q = _quat_from_axes(e1, e2)
        return {"finger": Lf, "stop": Ls, "quat": q, "stop_half": (0.5 * (hi_e1 - lo_e1), 0.5 * (hi_e2 - lo_e2), FW_HALF[2]),
                "bit": 1 << (2 + k_index)}

    # ---------------------------------------------------------------- MJCF
    def body_origin(self, eid):
        if eid == "final":
            return self.hinge if self.final == "lid" else self.drawer_origin()
        return self.el[eid]["mount"]

    def drawer_origin(self):
        D = self.shell["D"]
        dr = self.drawer
        return np.array([-D / 2, dr["u"], dr["v"]])

    def xml(self, name="box"):
        s = self.shell
        D, W, H = s["D"], s["W"], s["H"]
        col, trim, lidc = _f(s["rgba"]), _f(s["trim_rgba"]), _f(s.get("lid_rgba", s["rgba"]))
        dark = "0.08 0.08 0.08 1"
        out = []
        A = out.append
        A('<body name="{}">'.format(name))
        A('<geom name="sh_bottom" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([0, 0, T / 2]), _f([D / 2, W / 2, T / 2]), col, TOUCH))
        A('<geom name="sh_back" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([D / 2 - T / 2, 0, H / 2]), _f([T / 2, W / 2, H / 2]), col, TOUCH))
        A('<geom name="sh_left" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([0, W / 2 - T / 2, H / 2]), _f([D / 2 - T, T / 2, H / 2]), col, TOUCH))
        A('<geom name="sh_right" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([0, -W / 2 + T / 2, H / 2]), _f([D / 2 - T, T / 2, H / 2]), col, TOUCH))
        if self.final == "drawer":
            dr = self.drawer
            dw, dh = dr["w"], dr["h"]
            u, v = dr["u"], dr["v"]
            # front wall with a rectangular opening (four pieces)
            y0, y1 = u - dw / 2, u + dw / 2
            z0, z1 = v - dh / 2, v + dh / 2
            pieces = [((-W / 2, y0), (0, H)), ((y1, W / 2), (0, H)), ((y0, y1), (0, z0)), ((y0, y1), (z1, H))]
            for j, ((ya, yb), (za, zb)) in enumerate(pieces):
                if yb - ya < 1e-4 or zb - za < 1e-4:
                    continue
                A('<geom name="sh_front{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                    j, _f([-D / 2 + T / 2, (ya + yb) / 2, (za + zb) / 2]), _f([T / 2, (yb - ya) / 2, (zb - za) / 2]),
                    col, TOUCH))
            # fixed top
            A('<geom name="sh_top" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                _f([0, 0, H - T / 2]), _f([D / 2, W / 2, T / 2]), lidc, TOUCH))
        else:
            A('<geom name="sh_front" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                _f([-D / 2 + T / 2, 0, H / 2]), _f([T / 2, W / 2, H / 2]), col, TOUCH))
        # static decorations of elements (tracks, bezels, rails)
        for e in self.el.values():
            m, n = e["mount"], e["n"]
            if e["type"] == "button":
                A('<geom name="{}_bezel" type="cylinder" pos="{}" quat="{}" size="0.0135 0.0008" rgba="{}" {}/>'.format(
                    e["id"], _f(m + n * 0.0008), _f(_quat_z_to(n)), dark, VISUAL))
            elif e["type"] == "bolt":
                tr = e["travel"]
                A('<geom name="{}_track" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                    e["id"], _f(m + n * 0.0006), _f([0.0006, 0.025 + tr, 0.005]), dark, VISUAL))
                for sgn in (-1, 1):
                    A('<geom name="{}_keep{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                        e["id"], (sgn + 1) // 2, _f(m + n * 0.004 + np.array([0, 0, sgn * 0.0155])),
                        _f([0.004, 0.025 + tr, 0.0025]), trim, TOUCH))
            elif e["type"] == "panel":
                pw, ph, tr = e["w"], e["h"], e["travel"]
                up = e["slide"] == "up"
                if up:
                    for sgn in (-1, 1):
                        A('<geom name="{}_rail{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                            e["id"], (sgn + 1) // 2,
                            _f(m + n * 0.012 + np.array([0, sgn * (pw / 2 + 0.005), tr / 2])),
                            _f([0.012, 0.004, ph / 2 + tr / 2]), trim, TOUCH))
                else:
                    for sgn in (-1, 1):
                        A('<geom name="{}_rail{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                            e["id"], (sgn + 1) // 2,
                            _f(m + n * 0.012 + np.array([0, 0.0, sgn * (ph / 2 + 0.005)])),
                            _f([0.012, pw / 2 + tr, 0.004]), trim, TOUCH))      # symmetric: no direction cue
        for g in self._case_hidden_geoms():
            A(g)
        # moving elements
        for e in self.el.values():
            A(self._element_xml(e))
        A(self._final_xml())
        A('</body>')
        return "\n".join(out)

    def _hidden_geoms(self, eid):
        """Hidden interlock geoms carried by element eid (positions relative to its body origin)."""
        o = self.body_origin(eid)
        out = []
        attrs = 'condim="1" group="4" rgba="1 0 0 0" mass="0" solref="0.004 1" solimp="0.99 0.999 0.0005"'
        for p in self.pairs:
            if p["a"] == eid:
                for j, (c, q, h) in enumerate(p["segs"]):
                    out.append('<geom name="ib{}_{}" type="box" pos="{}" quat="{}" size="{}" contype="{}" conaffinity="{}" {}/>'.format(
                        p["i"], j, _f(c - o), _f(q), _f(h), p["bit"], p["bit"], attrs))
            if p["b"] == eid:
                out.append('<geom name="if{}" type="box" pos="{}" quat="{}" size="{}" contype="{}" conaffinity="{}" {}/>'.format(
                    p["i"], _f(p["finger"] - o), _f(p["quat"]), _f(p.get("fhalf", F_HALF)), p["bit"], p["bit"], attrs))
        return out

    def _case_hidden_geoms(self):
        """Fixed latch stops, each on its own joint-less child body of the case (a stop on the case
        body itself would be filtered out as a parent-child contact with the bolt)."""
        out = []
        attrs = 'condim="1" group="4" rgba="1 0 0 0" mass="0" solref="0.004 1" solimp="0.99 0.999 0.0005"'
        for eid, L in sorted(self.latches.items()):
            out.append('<body name="ls_{}_body"><geom name="ls_{}" type="box" pos="{}" quat="{}" size="{}" contype="{}" '
                       'conaffinity="{}" {}/></body>'.format(eid, eid, _f(L["stop"]), _f(L["quat"]), _f(L["stop_half"]),
                                                            L["bit"], L["bit"], attrs))
        return out

    def _element_xml(self, e):
        m, n = e["mount"], e["n"]
        trim = _f(e.get("rgba", self.shell["trim_rgba"]))
        eid = e["id"]
        out = ['<body name="{}" pos="{}" gravcomp="1">'.format(eid, _f(m))]
        fr = e.get("friction", 2.0)
        if e["type"] == "button":
            tr = e.get("travel", 0.010)
            out.append('<joint name="{}_j" type="slide" axis="{}" range="0 {}" frictionloss="{}" damping="15" armature="1.0" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                eid, _f(e["axis"]), tr, fr))
            out.append('<inertial pos="0 0 0" mass="0.02" diaginertia="1e-6 1e-6 1e-6"/>')
            out.append('<geom name="{}_cap" type="cylinder" pos="{}" quat="{}" size="0.009 0.010" rgba="{}" {} friction="1 0.01 0.001"/>'.format(
                eid, _f(n * 0.006), _f(_quat_z_to(n)), trim, TOUCH))
        elif e["type"] == "bolt":
            tr = e["travel"]
            out.append('<joint name="{}_j" type="slide" axis="{}" range="0 {}" frictionloss="{}" damping="15" armature="1.0" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                eid, _f(e["axis"]), tr, fr))
            out.append('<inertial pos="0 0 0" mass="0.05" diaginertia="1e-5 1e-5 1e-5"/>')
            out.append('<geom name="{}_plate" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                eid, _f(n * 0.0035), _f([0.003, 0.025, 0.012]), trim, TOUCH))
            knob = '<geom name="{}_knob" type="cylinder" pos="{}" quat="{}" size="0.0075 0.011" rgba="{}" {}/>'.format(
                eid, _f(n * (0.0065 + 0.011)), _f(_quat_z_to(n)), trim, TOUCH)
            if e.get("latched"):
                # the pin is its own body: pressing it in moves only the pin and the latch finger
                L = self.latches[eid]
                out.append('<body name="{}_pin" pos="0 0 0" gravcomp="1">'.format(eid))
                out.append('<joint name="{}_p" type="slide" axis="{}" range="0 {}" stiffness="{}" springref="0" frictionloss="0.3" damping="15" armature="1.0" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                    eid, _f(e["k"]), LATCH_IN, LATCH_K))
                out.append('<inertial pos="0 0 0" mass="0.01" diaginertia="1e-6 1e-6 1e-6"/>')
                out.append(knob)
                out.append('<geom name="lf_{}" type="box" pos="{}" quat="{}" size="{}" contype="{}" conaffinity="{}" '
                           'condim="1" group="4" rgba="1 0 0 0" mass="0" solref="0.004 1" solimp="0.99 0.999 0.0005"/>'.format(
                               eid, _f(L["finger"] - e["mount"]), _f(L["quat"]), _f(FW_HALF), L["bit"], L["bit"]))
                out.append('</body>')
            else:
                out.append(knob)
        elif e["type"] == "knob":
            out.append('<joint name="{}_j" type="hinge" axis="{}" range="0 {}" frictionloss="{}" damping="0.05" armature="0.05" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                eid, _f(e["axis"]), math.pi / 2, e.get("friction", 0.15)))
            out.append('<inertial pos="0 0 0" mass="0.03" diaginertia="2e-6 2e-6 2e-6"/>')
            out.append('<geom name="{}_base" type="cylinder" pos="{}" quat="{}" size="{} 0.006" rgba="{}" {}/>'.format(
                eid, _f(n * 0.006), _f(_quat_z_to(n)), KNOB_R, trim, TOUCH))
            out.append('<geom name="{}_fin" type="box" pos="{}" size="{}" rgba="{}" {} friction="1.2 0.02 0.002"/>'.format(
                eid, _f(n * 0.019), _f([0.007, 0.004, 0.030]), trim, TOUCH))
        elif e["type"] == "panel":
            tr = e["travel"]
            pw, ph = e["w"], e["h"]
            out.append('<joint name="{}_j" type="slide" axis="{}" range="0 {}" frictionloss="{}" damping="15" armature="1.0" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                eid, _f(e["axis"]), tr, fr))
            out.append('<inertial pos="0 0 0" mass="0.06" diaginertia="2e-5 2e-5 2e-5"/>')
            out.append('<geom name="{}_plate" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                eid, _f(n * 0.022), _f([0.002, pw / 2, ph / 2]), trim, TOUCH))
            if e["slide"] == "up":
                rib = n * 0.028 + np.array([0, 0, -ph / 2 + 0.004])
                out.append('<geom name="{}_rib" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                    eid, _f(rib), _f([0.006, pw * 0.3, 0.004]), trim, TOUCH))
            else:
                sd = float(e["dir"])
                rib = n * 0.028 + np.array([0, -sd * (pw / 2 - 0.004), 0])
                out.append('<geom name="{}_rib" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                    eid, _f(rib), _f([0.006, 0.004, ph * 0.3]), trim, TOUCH))
        out.extend(self._hidden_geoms(eid))
        out.append('</body>')
        return "\n".join(out)

    def _final_xml(self):
        s = self.shell
        D, W = s["D"], s["W"]
        lidc = _f(s.get("lid_rgba", s["rgba"]))
        trim = _f(s["trim_rgba"])
        if self.final == "lid":
            lw = s.get("lip_w", 0.09)
            lu = s.get("lip_u", 0.0)
            out = ['<body name="lid" pos="{}">'.format(_f(self.hinge))]
            out.append('<joint name="lid_j" type="hinge" axis="0 1 0" range="0 {}" frictionloss="{}" damping="0.3" armature="0.01" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
                LID_RANGE, s.get("lid_friction", 0.05)))
            out.append('<inertial pos="{}" mass="{}" diaginertia="0.002 0.003 0.004"/>'.format(
                _f([-D / 2, 0, T / 2]), s.get("lid_mass", 0.35)))
            out.append('<geom name="lid_plate" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                _f([-D / 2, 0, T / 2]), _f([D / 2 + 0.002, W / 2 + 0.002, T / 2]), lidc, TOUCH))
            out.append('<geom name="lid_lip" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                _f([-D - 0.014, lu, T / 2 - 0.002]), _f([0.014, lw / 2, T / 2 - 0.002]), lidc, TOUCH))
            out.extend(self._hidden_geoms("final"))
            out.append('</body>')
            return "\n".join(out)
        dr = self.drawer
        dw, dh, depth, tr = dr["w"], dr["h"], dr.get("depth", D - 2 * T - 0.01), dr["travel"]
        o = self.drawer_origin()
        out = ['<body name="drawer" pos="{}">'.format(_f(o))]
        out.append('<joint name="drawer_j" type="slide" axis="-1 0 0" range="0 {}" frictionloss="{}" damping="15" armature="1.5" solreflimit="0.004 1" solimplimit="0.95 0.99 0.0005" solreffriction="0.004 1" solimpfriction="0.99 0.999 0.001"/>'.format(
            tr, dr.get("friction", 2.0)))
        out.append('<inertial pos="{}" mass="0.3" diaginertia="0.001 0.001 0.001"/>'.format(_f([depth / 2, 0, 0])))
        # front plate flush with the face, slightly smaller than the opening
        out.append('<geom name="dr_front" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([T / 2, 0, 0]), _f([T / 2, dw / 2 - 0.002, dh / 2 - 0.002]), lidc, TOUCH))
        bw = dw / 2 - 0.006
        out.append('<geom name="dr_bottom" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([T + depth / 2, 0, -dh / 2 + 0.006]), _f([depth / 2, bw, 0.004]), lidc, TOUCH))
        for sgn in (-1, 1):
            out.append('<geom name="dr_side{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                (sgn + 1) // 2, _f([T + depth / 2, sgn * (bw - 0.004), -0.003]), _f([depth / 2, 0.004, dh / 2 - 0.012]),
                lidc, TOUCH))
        out.append('<geom name="dr_back" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
            _f([T + depth - 0.004, 0, -0.003]), _f([0.004, bw, dh / 2 - 0.012]), lidc, TOUCH))
        hl = dr.get("handle_len", 0.04)
        so = 0.045
        out.append('<geom name="dr_handle" type="capsule" fromto="{}" size="0.006" rgba="{}" {} friction="1.5 0.02 0.002"/>'.format(
            _f([-so, 0, -hl / 2, -so, 0, hl / 2]), trim, TOUCH))
        for sgn in (-1, 1):
            out.append('<geom name="dr_post{}" type="box" pos="{}" size="{}" rgba="{}" {}/>'.format(
                (sgn + 1) // 2, _f([-so / 2, 0, sgn * (hl / 2 - 0.004)]), _f([so / 2, 0.004, 0.004]), trim, TOUCH))
        out.extend(self._hidden_geoms("final"))
        out.append('</body>')
        return "\n".join(out)

    # ---------------------------------------------------------------- scoring meta
    def meta(self):
        """Stage list for scoring: element joint, unlock threshold or window (joint units), kind."""
        req = self.cfg["required"]
        stages = []
        for eid in req:
            if eid == "final":
                if self.final == "lid":
                    stages.append({"id": "final", "joint": "lid_j", "threshold": math.radians(60.0), "kind": "lid"})
                else:
                    stages.append({"id": "final", "joint": "drawer_j", "threshold": 0.7 * self.drawer["travel"],
                                   "kind": "drawer"})
                continue
            e = self.el[eid]
            full = math.pi / 2 if e["type"] == "knob" else e.get("travel", 0.010)
            st = {"id": eid, "joint": eid + "_j", "threshold": 0.8 * full, "kind": e["type"]}
            if e.get("window") is not None:
                st["window"] = [float(e["window"][0]), float(e["window"][1])]
                st["threshold"] = float(e["window"][0])
            stages.append(st)
        blk = {}
        for a, b in self.locks:
            blk.setdefault(b, []).append(a)
        req_set = set(req)
        for st in stages:
            st["blockers"] = [a for a in blk.get(st["id"], []) if a in req_set]
        joints = [eid + "_j" for eid in self.el] + [eid + "_p" for eid in self.latches]
        joints.append("lid_j" if self.final == "lid" else "drawer_j")
        return {"stages": stages, "joints": joints, "footprint": [self.shell["D"], self.shell["W"], self.shell["H"]]}


def standalone_xml(box, pos=(0, 0, 0)):
    return ('<mujoco model="boxcheck"><compiler angle="radian" autolimits="true"/>'
            '<option timestep="0.002" integrator="implicitfast" cone="elliptic" impratio="2" gravity="0 0 -9.81"/>'
            '<worldbody><body pos="{}">{}</body></worldbody></mujoco>').format(_f(pos), box.xml())


def check_box(cfg, verbose=False):
    """Simulation check of every interlock (no robot): blocked while a blocker is home (or outside its
    window), free once every blocker is released (inside its window), blockers free while their
    blocked part is loaded, latched bolts blocked unless their pin is pressed, the required list
    completable in order, and the later stages locked from home. Returns a report."""
    import mujoco
    box = Box(cfg)
    m = mujoco.MjModel.from_xml_string(standalone_xml(box))
    rep = {"ok": True, "checks": []}

    def jid(eid):
        return m.joint(("lid_j" if box.final == "lid" else "drawer_j") if eid == "final" else eid + "_j")

    def full(eid):
        if eid == "final":
            return LID_RANGE if box.final == "lid" else box.drawer["travel"]
        e = box.el[eid]
        return math.pi / 2 if e["type"] == "knob" else e.get("travel", 0.010)

    def release(eid):
        """The credit position (rev-redteam F2: chain credit needs every blocker there): window centre,
        else the credit threshold (80 % of travel), which must already release the dependents."""
        if eid != "final" and box.el[eid].get("window") is not None:
            lo, hi = box.el[eid]["window"]
            return 0.5 * (lo + hi)
        if eid == "final":
            return full(eid)
        return 0.8 * full(eid) + (math.radians(0.5) if box.el[eid]["type"] == "knob" else 0.0002)

    def thr(eid):
        if eid == "final":
            return math.radians(60) if box.final == "lid" else 0.7 * box.drawer["travel"]
        return 0.8 * full(eid)

    def play(eid):
        """Largest motion a blocked part may show: free play plus contact softness, and always below the
        start of the part's own release window (so a blocked part can never sit inside its window)."""
        if eid == "final":
            return math.radians(2.0) if box.final == "lid" else GAP + 0.0015
        e = box.el[eid]
        p = math.radians(12.0) if e["type"] == "knob" else GAP + 0.0015
        if e.get("window") is not None:
            p = min(p, e["window"][0] - (math.radians(2.0) if e["type"] == "knob" else 0.0003))
        return p

    def force_for(eid):
        if eid == "final":
            return 5.0 if box.final == "lid" else 30.0
        return 0.8 if box.el[eid]["type"] == "knob" else 30.0

    def push_free(state, free, eid, force, ramp=1.5, hold=0.5):
        """Load ramped 0 -> force over `ramp` s, then held. Parts in `state` are locked; parts in `free`
        only START at the given positions and are held by their own joint friction alone."""
        d = mujoco.MjData(m)
        saved = {}
        for k, v in state.items():
            jj = jid(k)
            saved[jj.id] = m.jnt_range[jj.id].copy()
            m.jnt_range[jj.id] = [v - 1e-6, v + 1e-6]
            d.qpos[jj.qposadr[0]] = v
        for k, v in free.items():
            d.qpos[jid(k).qposadr[0]] = v
        mujoco.mj_forward(m, d)
        j = jid(eid)
        n_r, n_h = int(ramp / m.opt.timestep), int(hold / m.opt.timestep)
        for t in range(n_r + n_h):
            d.qfrc_applied[:] = 0
            d.qfrc_applied[j.dofadr[0]] = force * min(1.0, (t + 1) / n_r)
            mujoco.mj_step(m, d)
        for jid_, r in saved.items():
            m.jnt_range[jid_] = r
        return float(d.qpos[j.qposadr[0]]), {k: float(d.qpos[jid(k).qposadr[0]]) for k in free}

    def push(state, eid, force, seconds=1.0, press_latch=True):
        """Apply a joint force/torque on eid for `seconds`; parts in `state` are LOCKED at the given
        positions (their joint range is narrowed), so the check measures the hidden geometry alone."""
        d = mujoco.MjData(m)
        saved = {}
        for k, v in state.items():
            jj = jid(k)
            saved[jj.id] = m.jnt_range[jj.id].copy()
            m.jnt_range[jj.id] = [v - 1e-6, v + 1e-6]
            d.qpos[jj.qposadr[0]] = v
        mujoco.mj_forward(m, d)
        j = jid(eid)
        lat = m.joint(eid + "_p") if (eid in box.latches) else None
        for _ in range(int(seconds / m.opt.timestep)):
            d.qfrc_applied[:] = 0
            d.qfrc_applied[j.dofadr[0]] = force
            if lat is not None and press_latch:
                d.qfrc_applied[lat.dofadr[0]] = 8.0
            mujoco.mj_step(m, d)
        for jid_, r in saved.items():
            m.jnt_range[jid_] = r
        return float(d.qpos[j.qposadr[0]]), d

    def ok_add(c):
        rep["checks"].append(c)
        rep["ok"] &= bool(c["ok"])

    blockers = {}
    for a, b in box.locks:
        blockers.setdefault(b, []).append(a)

    def upstream(eid, skip=None):
        """All parts upstream of eid (its blockers, their blockers, ...) at their release positions."""
        out, stack = {}, [x for x in blockers.get(eid, []) if x != skip]
        while stack:
            x = stack.pop()
            if x in out:
                continue
            out[x] = release(x)
            stack.extend(blockers.get(x, []))
        return out

    # 1) pairs: blocked with a home / at its ends / outside its window; free once all released
    for a, b in box.locks:
        others = upstream(b, skip=a)
        for x in list(others):
            if x == a:
                others.pop(x)
        tests = [0.0]
        if box.el[a].get("window") is not None:
            lo, hi = box.el[a]["window"]
            tests += [full(a), max(0.0, lo - (0.003 if box.el[a]["type"] != "knob" else math.radians(8))),
                      min(full(a), hi + (0.003 if box.el[a]["type"] != "knob" else math.radians(8)))]
        for qa in tests:
            st = dict(others)
            if qa > 0:
                st[a] = qa
            q, _ = push(st, b, force_for(b))
            ok_add({"check": "blocked", "a": a, "b": b, "qa": qa, "q": q, "ok": q < min(max(0.3 * thr(b), play(b)), play(b))})
    for b, al in blockers.items():
        st = upstream(b)
        q, _ = push(st, b, force_for(b), 1.5)
        ok_add({"check": "released", "b": b, "q": q, "ok": q > thr(b)})
        # the whole credit window releases (rev-quality R1): blocker near either window edge
        for a in al:
            if box.el[a].get("window") is None:
                continue
            lo, hi = box.el[a]["window"]
            eps = math.radians(1.0) if box.el[a]["type"] == "knob" else 0.0003
            for qa in (lo + eps, hi - eps):
                st2 = dict(st)
                st2[a] = qa
                q, _ = push(st2, b, force_for(b), 1.5)
                ok_add({"check": "released_at_window_edge", "a": a, "b": b, "qa": qa, "q": q, "ok": q > thr(b)})
    # 2) latched bolts: blocked without pressing the pin, free with it pressed
    for eid in box.latches:
        st = upstream(eid)
        q, _ = push(st, eid, force_for(eid), 1.0, press_latch=False)
        ok_add({"check": "latch_holds", "e": eid, "q": q, "ok": q < 0.3 * thr(eid)})
        q, _ = push(st, eid, force_for(eid), 1.5, press_latch=True)
        ok_add({"check": "latch_releases", "e": eid, "q": q, "ok": q > thr(eid)})
    # 3) unblocked parts move freely from home
    for eid in list(box.el) + ["final"]:
        if eid in blockers:
            continue
        q, _ = push({}, eid, force_for(eid), 1.5)
        ok_add({"check": "free", "e": eid, "q": q, "ok": q > thr(eid)})
    # 4) blockers are not jammed by a loaded finger (their own blockers released)
    for a, b in box.locks:
        d = mujoco.MjData(m)
        ja, jb = jid(a), jid(b)
        for x in blockers.get(a, []):
            d.qpos[jid(x).qposadr[0]] = release(x)
        for x in blockers.get(b, []):
            if x != a:
                d.qpos[jid(x).qposadr[0]] = release(x)
        mujoco.mj_forward(m, d)
        lat = m.joint(a + "_p") if a in box.latches else None
        for _ in range(750):
            d.qfrc_applied[:] = 0
            d.qfrc_applied[jb.dofadr[0]] = 0.3 * force_for(b)
            d.qfrc_applied[ja.dofadr[0]] = force_for(a)
            if lat is not None:
                d.qfrc_applied[lat.dofadr[0]] = 8.0
            mujoco.mj_step(m, d)
        qa = float(d.qpos[ja.qposadr[0]])
        ok_add({"check": "blocker_free_under_load", "a": a, "b": b, "qa": qa, "ok": qa > thr(a) or
                (box.el[a].get("window") is not None and qa > box.el[a]["window"][0])})
    # 5) the required list completes in order (window parts set to their window centre)
    state = {}
    for eid in cfg["required"]:
        q, _ = push(state, eid, force_for(eid), 1.5)
        ok_add({"check": "chain", "e": eid, "q": q, "ok": q > (thr(eid) if eid == "final" or
                                                                 box.el[eid].get("window") is None else box.el[eid]["window"][0])})
        state[eid] = release(eid)
    # 6) nothing blocked can be completed from home
    for eid in cfg["required"]:
        if eid not in blockers:
            continue
        q, _ = push({}, eid, force_for(eid))
        ok_add({"check": "locked_from_home", "e": eid, "q": q, "ok": q < play(eid)})
    # 7) forced bypass (rev-redteam F1): ramps up to 2x the largest load the robot can apply
    #    (measured: 80 N peak push, lever torque <= 80 N x 0.03 m = 2.4 N m; R/tM/fix_r1/robot_force2.log)
    for eid in list(box.el) + ["final"]:
        if eid not in blockers:
            continue
        if eid == "final":
            levels = (4.0, 8.0, 16.0, 24.0, 32.0, 40.0) if box.final == "lid" else (20.0, 40.0, 80.0, 120.0, 160.0, 200.0)
        elif box.el[eid]["type"] == "knob":
            levels = (0.5, 1.0, 2.0, 3.5, 5.0, 6.0, 8.0)
        else:
            levels = (20.0, 40.0, 80.0, 120.0, 160.0, 200.0)
        states = [{}] + [upstream(eid, skip=a) for a in blockers[eid]] if len(blockers[eid]) > 1 else [{}]
        for st in states:
            for f in levels:
                q, _ = push(st, eid, f, 1.5, press_latch=False)
                ok_add({"check": "forced_bypass", "e": eid, "state": sorted(st), "load": f, "q": q, "ok": q < play(eid)})
    for eid in box.latches:
        st = upstream(eid)
        for f in (20.0, 40.0, 80.0, 120.0, 160.0, 200.0):
            q, _ = push(st, eid, f, 1.5, press_latch=False)
            ok_add({"check": "forced_latch", "e": eid, "load": f, "q": q, "ok": q < 0.3 * thr(eid)})
    # 8) window back-drive (rev-quality r2, mech-qa r2 D1 b): a window blocker parked outside its window,
    #    held by its own friction only, stays blocking and does not move more than 0.1 mm (0.4 deg) while
    #    its dependent is loaded up to 2x the robot's measured maxima (80 N push, 2.4 N m lever, 80 N at
    #    the lid lip ~ 15 N m)
    def loads_for(b):
        if b == "final":
            return (2.0, 8.0, 15.0, 30.0) if box.final == "lid" else (40.0, 80.0, 160.0)
        if box.el[b]["type"] == "knob":
            return (0.31, 1.0, 2.4, 4.8)
        return (40.0, 80.0, 160.0)

    for a, b in box.locks:
        A = box.el[a]
        win = A.get("window")
        if win is None:
            continue
        knob = A["type"] == "knob"
        lo, hi = win
        fa = full(a)
        offs = [math.radians(x) for x in (1.0, 3.0, 6.0, 8.0)] if knob else [0.0003, 0.001, 0.0015, 0.002, 0.003]
        parks = [0.0, fa] + [lo - x for x in offs if lo - x >= 0.0] + [hi + x for x in offs if hi + x <= fa]
        tol = math.radians(0.4) if knob else 0.0001
        locked = dict(upstream(b, skip=a))
        locked.pop(a, None)
        locked.update({k: v for k, v in upstream(a).items() if k != b})
        for qa in sorted(set(round(x, 6) for x in parks)):
            for f in loads_for(b):
                q, fr = push_free(locked, {a: qa}, b, f)
                moved = abs(fr[a] - qa)
                # a blocker parked at its own end stop may only be pushed further into that stop
                ok_add({"check": "back_drive", "a": a, "b": b, "qa": qa, "load": f, "qa_end": fr[a], "q": q,
                        "ok": (q < play(b)) and moved <= tol and not (lo <= fr[a] <= hi)})
    if verbose:
        for c in rep["checks"]:
            print(json.dumps(c))
    return rep


if __name__ == "__main__":
    import sys
    cfg = json.load(open(sys.argv[1]))
    r = check_box(cfg, verbose=True)
    print("OK" if r["ok"] else "FAIL")
