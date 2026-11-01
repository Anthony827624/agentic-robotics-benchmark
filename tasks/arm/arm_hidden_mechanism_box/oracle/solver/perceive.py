"""Oracle perception: box pose (x, y, yaw) from the two fixed RGB cameras at the episode start.

Privileged knowledge used: the box's top outline (dimensions, lip) and top height. The pose is
estimated by segmenting the top face by colour around the nominal box position, back-projecting
the pixels onto the known top plane and fitting the known outline.
"""

import math

import numpy as np

TABLE_Z = 0.80
CAMS = {
    "left": ((-0.42, 0.42, 1.30), (0.0 + 0.0, 0.0, 0.86), 42.0, 320, 240),
    "right": ((-0.42, -0.42, 1.30), (0.0, 0.0, 0.86), 42.0, 320, 240),
}
NOMINAL = (0.04, 0.0)


def look_at(pos, target, up=(0, 0, 1)):
    pos, target = np.asarray(pos, float), np.asarray(target, float)
    f = target - pos
    f = f / np.linalg.norm(f)
    z = -f
    x = np.cross(np.array(up, dtype=float), z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=1)


def cam_matrices(name):
    pos, tgt, fovy, w, h = CAMS[name]
    R = look_at(pos, tgt) @ np.diag([1.0, -1.0, -1.0])
    f = (h / 2.0) / math.tan(math.radians(fovy) / 2.0)
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
    return K, R, np.asarray(pos, float)


def project(K, R, C, P):
    pc = (R.T @ (np.asarray(P, float) - C).T).T
    uv = (K @ pc.T).T
    return uv[:, :2] / uv[:, 2:3]


def backproject(K, R, C, uv, z):
    ones = np.ones((len(uv), 1))
    rays = (R @ (np.linalg.inv(K) @ np.hstack([uv, ones]).T)).T
    t = (z - C[2]) / rays[:, 2]
    return C + rays * t[:, None]


def top_outline(D, W, lip=None):
    """Polygons (box frame, xy) of the top as seen from above: main rectangle + optional lip."""
    polys = [np.array([[-D / 2, -W / 2], [D / 2, -W / 2], [D / 2, W / 2], [-D / 2, W / 2]])]
    if lip is not None:
        lu, lw = lip
        polys.append(np.array([[-D / 2 - 0.028, lu - lw / 2], [-D / 2, lu - lw / 2], [-D / 2, lu + lw / 2],
                               [-D / 2 - 0.028, lu + lw / 2]]))
    return polys


def inside(polys, pts):
    """Boolean mask: points inside any convex polygon (counter-clockwise)."""
    res = np.zeros(len(pts), bool)
    for P in polys:
        ok = np.ones(len(pts), bool)
        for i in range(len(P)):
            a, b = P[i], P[(i + 1) % len(P)]
            e = b - a
            ok &= (e[0] * (pts[:, 1] - a[1]) - e[1] * (pts[:, 0] - a[0])) >= 0
        res |= ok
    return res


def segment(img, seed_uv, tol=22.0):
    import cv2
    h, w = img.shape[:2]
    u, v = int(round(seed_uv[0])), int(round(seed_uv[1]))
    u, v = min(max(u, 3), w - 4), min(max(v, 3), h - 4)
    blur = cv2.GaussianBlur(img, (3, 3), 0).astype(np.float32)
    ref = np.median(blur[v - 3:v + 4, u - 3:u + 4].reshape(-1, 3), axis=0)
    close = np.max(np.abs(blur - ref), axis=2) < tol
    n, lab = cv2.connectedComponents(close.astype(np.uint8), connectivity=4)
    if lab[v, u] == 0:
        return np.zeros((h, w), bool)
    return lab == lab[v, u]


def estimate_pose(images, D, W, z_top, lip=None, nominal=NOMINAL):
    """images: {"left": HxWx3 RGB, "right": ...}. Returns (x, y, yaw) and diagnostics."""
    pts = []
    for name, img in images.items():
        if img is None or img.size == 0:
            continue
        K, R, C = cam_matrices(name)
        seed = project(K, R, C, [[nominal[0] + 0.02, nominal[1], z_top]])[0]
        m = segment(img, seed)
        vv, uu = np.nonzero(m)
        if len(uu) < 50:
            continue
        P = backproject(K, R, C, np.stack([uu + 0.5, vv + 0.5], 1), z_top)
        pts.append(P[:, :2])
    if not pts:
        return (nominal[0], nominal[1], 0.0), {"ok": False}
    pts = np.concatenate(pts)
    c = pts.mean(0)
    X = pts - c
    ev, evec = np.linalg.eigh(X.T @ X / len(X))
    major = evec[:, 1]                          # long axis = box y axis
    yaw = math.atan2(-major[0], major[1])
    yaw = (yaw + math.pi / 2) % math.pi - math.pi / 2
    polys = top_outline(D, W, lip)

    def score(x, y, a):
        cs, sn = math.cos(a), math.sin(a)
        loc = (pts - [x, y]) @ np.array([[cs, -sn], [sn, cs]])
        ins = inside(polys, loc)
        return ins.sum() - 2.0 * (~ins).sum()

    best = (c[0] + 0.01, c[1], yaw)
    bs = score(*best)
    for step_xy, step_a, n in ((0.006, math.radians(2.0), 3), (0.002, math.radians(0.7), 3), (0.001, math.radians(0.3), 2)):
        improved = True
        it = 0
        while improved and it < 30:
            improved = False
            it += 1
            x0, y0, a0 = best
            for dx in range(-n, n + 1):
                for dy in range(-n, n + 1):
                    for da in (-1, 0, 1):
                        cand = (x0 + dx * step_xy, y0 + dy * step_xy, a0 + da * step_a)
                        s = score(*cand)
                        if s > bs:
                            bs, best, improved = s, cand, True
    return best, {"ok": True, "n": int(len(pts)), "score": float(bs)}
