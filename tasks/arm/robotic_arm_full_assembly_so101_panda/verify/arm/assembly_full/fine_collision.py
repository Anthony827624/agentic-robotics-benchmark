"""Finer collision hulls for the parts whose convex decomposition bulges into a mating part along a real insertion path
(the brackets: the kit's CoACD hulls of base_clamp penetrate the base by 2.2 mm on the way in, the true meshes do not,
path_check.py and DESIGN.md 7).

`fine_pieces(a2_dir, name, parts, graph, cfg, threshold, cache_dir)`: CoACD of the part's visual mesh at `threshold`
(the kit's hulls use CoACD's default 0.05), then the same carve and clip as the six-servo cell (`carved_collision`);
cached as a pickle of the pieces (trimesh, mm) under cache_dir.
"""
import hashlib
import os
import pickle


def fine_pieces(a2_dir, name, parts, graph, cfg, threshold, cache_dir, log=print):
    import numpy as np
    import trimesh
    from arm.assembly_six.cell_builder import _clip_to_bbox, _keepout_boxes, carve_hulls
    p = parts[name]
    src = os.path.join(a2_dir, p["mesh_visual"])
    h = hashlib.sha256(open(src, "rb").read() + repr((threshold, cfg["carve_margin_mm"], cfg["carve_sweep_extra_mm"])).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, "fine_%s_%s.pkl" % (name, h))
    if os.path.isfile(path):
        return pickle.load(open(path, "rb"))
    import coacd
    mesh = trimesh.load(src, force="mesh")
    parts_out = coacd.run_coacd(coacd.Mesh(np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)), threshold=threshold,
                                preprocess_mode="auto", max_convex_hull=-1, seed=0)
    hulls = [trimesh.Trimesh(v, f).convex_hull for v, f in parts_out]
    boxes = _keepout_boxes(name, parts, graph["mates"], None, cfg["carve_margin_mm"], cfg["carve_sweep_extra_mm"])
    pieces = carve_hulls(hulls, boxes, cfg["carve_min_volume_mm3"], cfg["carve_min_thickness_mm"]) if boxes else hulls
    pieces = _clip_to_bbox(pieces, p["bbox_mm"], min_volume_mm3=cfg["carve_min_volume_mm3"])
    log("[fine] %s: %d hulls at threshold %.3f (the kit's: %d)" % (name, len(pieces), threshold, len(p["mesh_collision"])))
    os.makedirs(cache_dir, exist_ok=True)
    pickle.dump(pieces, open(path, "wb"))
    return pieces


def _manifold(mesh):
    import numpy as np
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32), tri_verts=np.asarray(mesh.faces, np.uint32)))


def _union(mans):
    import manifold3d as mf
    try:
        return mf.Manifold.batch_boolean(mans, mf.OpType.Add)
    except Exception:
        out = mans[0]
        for x in mans[1:]:
            out = out + x
        return out


def _trimesh(man):
    import numpy as np
    import trimesh
    mm = man.to_mesh()
    return trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3].astype(float), np.asarray(mm.tri_verts).astype(int), process=True)


def carve_by_part(pieces, other, extract_dir, sweep_mm, margin_mm=0.3, threshold=0.03, min_volume_mm3=1.0, max_hulls=12, log=print, label=""):
    """The convex pieces (trimesh, mm, this part's frame) minus the TRUE mesh of a part it receives (`other`, at its home
    pose in this frame) swept along `extract_dir` over `sweep_mm` and grown by `margin_mm`: every piece that overlaps the
    swept volume is replaced by a CoACD decomposition (at `threshold`) of the piece minus that volume. Where two printed
    parts meet face to face, a convex piece spanning a concavity bulges into the other part (the brackets in the base and
    the forearm: 1.7-2.2 mm, the true meshes touch); the result follows the true surface."""
    import numpy as np
    import coacd
    import trimesh
    ext = np.asarray(extract_dir, float)
    ext = ext / np.linalg.norm(ext)
    o = _manifold(other)
    sweep = o
    n = max(1, int(np.ceil(sweep_mm / 1.0)))
    for k in range(1, n + 1):
        sweep = sweep + o.translate(tuple((ext * sweep_mm * k / n).tolist()))
    grown = sweep
    for ax in np.eye(3):
        for s in (-1.0, 1.0):
            grown = grown + sweep.translate(tuple((ax * s * margin_mm).tolist()))
    out, n_hit = [], 0
    for pc in pieces:
        P = _manifold(pc)
        if (P ^ grown).volume() < min_volume_mm3:
            out.append(pc)
            continue
        n_hit += 1
        rest = P - grown
        if rest.volume() < min_volume_mm3:
            continue
        rm = _trimesh(rest)
        for v, f in coacd.run_coacd(coacd.Mesh(np.asarray(rm.vertices, float), np.asarray(rm.faces, int)), threshold=threshold,
                                    preprocess_mode="off", max_convex_hull=max_hulls, seed=0):
            h = trimesh.Trimesh(v, f).convex_hull
            if h.volume >= min_volume_mm3:
                out.append(h)
    log("[fine] %s: %d of %d pieces carved by the received part, %d pieces now" % (label, n_hit, len(pieces), len(out)))
    return out


def carrier_pieces(a2_dir, carrier, bracket, parts, graph, cfg, cache_dir, threshold=0.03, margin_mm=0.8, bracket_pieces=None, log=print):
    """The carrier's six-servo collision pieces carved by its bracket along the bracket's insertion path (cached). With
    `bracket_pieces` (the bracket's own collision pieces, trimesh mm) the carve follows the bracket as it is simulated
    (its convex pieces bulge up to ~1.4 mm out of its true mesh, which jammed it 14 mm short of home); else its true
    mesh."""
    import numpy as np
    import trimesh
    from arm.assembly_six.cell_builder import carved_collision, pose_mm, inv
    mm = [x for x in graph["mates"] if x["kind"] == "part_seat" and x["part_a"] == carrier and x["part_b"] == bracket][0]
    src = [os.path.join(a2_dir, parts[n]["mesh_visual"]) for n in (carrier, bracket)]
    hp = hashlib.sha256(b"".join(np.ascontiguousarray(pc.vertices, float).tobytes() for pc in bracket_pieces)).hexdigest()[:12] if bracket_pieces else ""
    h = hashlib.sha256(b"".join(open(p, "rb").read() for p in src) + repr((threshold, margin_mm, 12, hp, mm["frame_a_in_a"], mm["frame_b_in_b"], mm["insertion_axis_in_a"],
                       cfg["carve_margin_mm"], cfg["carve_sweep_extra_mm"])).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, "carrier_%s_%s_%s.pkl" % (carrier, bracket, h))
    if os.path.isfile(path):
        return pickle.load(open(path, "rb"))
    pieces = carved_collision(a2_dir, parts[carrier], parts, graph, cfg)
    T = pose_mm(mm["frame_a_in_a"]) @ inv(pose_mm(mm["frame_b_in_b"]))       # the bracket at home in the carrier frame
    T[:3, 3] *= 1000.0                                                          # the meshes are in mm
    if bracket_pieces:
        b = _trimesh(_union([_manifold(pc.convex_hull) for pc in bracket_pieces]))
    else:
        b = trimesh.load(src[1], force="mesh")
    b.apply_transform(T)
    depth = float(mm.get("insertion_depth_mm") or 5.0) + 20.0
    out = carve_by_part(pieces, b, -np.asarray(mm["insertion_axis_in_a"], float), depth, margin_mm=margin_mm, threshold=threshold, log=log,
                        label="%s by %s" % (carrier, bracket))
    os.makedirs(cache_dir, exist_ok=True)
    pickle.dump(out, open(path, "wb"))
    return out


def link_pieces(a2_dir, link, other, T_other_in_link_m, extract_dir_in_link, sweep_mm, parts, graph, cfg, cache_dir, base_pieces=None,
                threshold=0.03, margin_mm=0.5, other_pieces=None, log=print):
    """A horn-side link's collision pieces carved by the true mesh of a part it slides past on its insertion path
    (`other`, at its home pose in the link's design frame, swept relative to the link along `extract_dir_in_link`)."""
    import numpy as np
    import trimesh
    from arm.assembly_six.cell_builder import carved_collision
    T = np.asarray(T_other_in_link_m, float).copy()
    T[:3, 3] *= 1000.0
    src = [os.path.join(a2_dir, parts[n]["mesh_visual"]) for n in (link, other)]
    h = hashlib.sha256(b"".join(open(p, "rb").read() for p in src) + repr((threshold, margin_mm, 12, np.round(T, 4).tolist(), np.round(extract_dir_in_link, 6).tolist(),
                       sweep_mm, cfg["carve_margin_mm"], cfg["carve_sweep_extra_mm"], base_pieces is None, other_pieces is None)).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, "link_%s_%s_%s.pkl" % (link, other, h))
    if os.path.isfile(path):
        return pickle.load(open(path, "rb"))
    pieces = base_pieces if base_pieces is not None else carved_collision(a2_dir, parts[link], parts, graph, cfg)
    b = _trimesh(_union([_manifold(pc.convex_hull) for pc in other_pieces])) if other_pieces else trimesh.load(src[1], force="mesh")
    b.apply_transform(T)
    out = carve_by_part(pieces, b, extract_dir_in_link, sweep_mm, margin_mm=margin_mm, threshold=threshold, log=log, label="%s by %s" % (link, other))
    os.makedirs(cache_dir, exist_ok=True)
    pickle.dump(out, open(path, "wb"))
    return out
