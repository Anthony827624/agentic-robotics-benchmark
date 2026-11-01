"""rio: boilerplate I/O for robocasa_object_dynamics_from_video (no perception, tracking or identification code).

    import rio
    cams = rio.cameras()                         # list of dicts: name, K, R_w2c, t_w2c, width, height
    frames = rio.load_video("A", 0, "cam1")      # (N, H, W, 3) uint8 RGB
    clip = rio.clip_info("A", 0)                 # kind, fps, n_frames, stop_bar, probe_500hz (M,3), arm_q (N,7)
    uv = rio.project(cams[0], X_world)           # (n,2) pixels of world points (n,3)
    rio.write_params({"A": {...}, "B": {...}, "C": {...}})
"""
import json
import os

import numpy as np

DATA = os.path.join(os.path.expanduser("~"), "data")
SUBMISSION = os.path.join(os.path.expanduser("~"), "submission", "params.json")


def cameras():
    with open(os.path.join(DATA, "cameras.json")) as f:
        cams = json.load(f)["cameras"]
    for c in cams:
        c["K"] = np.array(c["K"])
        c["R_w2c"] = np.array(c["R_w2c"])
        c["t_w2c"] = np.array(c["t_w2c"])
    return cams


def object_dir(obj):
    return os.path.join(DATA, "objects", obj)


def object_info(obj):
    with open(os.path.join(object_dir(obj), "object.json")) as f:
        return json.load(f)


def clip_info(obj, i):
    d = os.path.join(object_dir(obj), "clip%02d" % i)
    with open(os.path.join(d, "clip.json")) as f:
        info = json.load(f)
    info["probe_500hz"] = np.load(os.path.join(d, "probe_500hz.npy"), allow_pickle=False)
    info["arm_q"] = np.load(os.path.join(d, "arm_q.npy"), allow_pickle=False)
    return info


def load_video(obj, i, cam):
    import cv2
    cap = cv2.VideoCapture(os.path.join(object_dir(obj), "clip%02d" % i, cam + ".mp4"))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f[:, :, ::-1])
    cap.release()
    return np.stack(out)


def project(cam, X):
    X = np.atleast_2d(np.asarray(X, float))
    Xc = X @ cam["R_w2c"].T + cam["t_w2c"]
    uv = Xc @ cam["K"].T
    return uv[:, :2] / uv[:, 2:3]


def write_params(objects):
    """objects: {"A": {"com": [3], "inertia": 3x3, "mu": float}, ...} -> ~/submission/params.json.
    Checks the same structural rules as the grader (finite numbers, symmetric positive-definite inertia that
    satisfies the triangle inequality); raises ValueError otherwise."""
    out = {}
    for k, e in objects.items():
        com = [float(x) for x in e["com"]]
        I = np.array(e["inertia"], float).reshape(3, 3)
        if not (np.all(np.isfinite(com)) and np.all(np.isfinite(I)) and np.isfinite(float(e["mu"]))):
            raise ValueError("%s: non-finite value" % k)
        I = 0.5 * (I + I.T)
        ev = np.sort(np.linalg.eigvalsh(I))
        if ev[0] <= 0 or ev[0] + ev[1] < ev[2] * (1 - 1e-6):
            raise ValueError("%s: inertia must be positive definite and satisfy the triangle inequality" % k)
        out[k] = {"com": com, "inertia": I.tolist(), "mu": float(e["mu"])}
    os.makedirs(os.path.dirname(SUBMISSION), exist_ok=True)
    with open(SUBMISSION, "w") as f:
        json.dump({"objects": out}, f, indent=1)
    return SUBMISSION
