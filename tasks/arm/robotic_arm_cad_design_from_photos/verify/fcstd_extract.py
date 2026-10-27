"""Read the submitted FreeCAD document into plain data (the grader's only contact with FreeCAD).

Runs under FreeCAD 1.1.4's headless interpreter as an unprivileged account (verify.py starts it with a time limit):

    freecadcmd -c "import sys; sys.argv = ['fcstd_extract.py', FCSTD, SERVO_STEP, OUT_DIR]; exec(open(PATH).read())"

It opens the document, finds the Assembly, and writes OUT_DIR/model.json plus one STEP per shape-bearing leaf of every
assembly component, in world coordinates (the document as saved). Joints are read through FreeCAD's own Assembly
helpers, so a joint built in the GUI and one built by a script read the same way. Nothing in the document is executed
beyond what FreeCAD runs to open and recompute it. Errors are recorded in model.json, never raised.
"""
import json
import os
import sys
import traceback

import FreeCAD as App
import Part

SERVO_VOLUME_REL_TOL = 0.01
SERVO_EXTENT_TOL_MM = 0.5


def matrix(placement):
    m = placement.toMatrix()
    return [[m.A11, m.A12, m.A13, m.A14], [m.A21, m.A22, m.A23, m.A24], [m.A31, m.A32, m.A33, m.A34], [0.0, 0.0, 0.0, 1.0]]


def extents(shape):
    b = shape.BoundBox
    return sorted([b.XLength, b.YLength, b.ZLength])


def leaf_label(obj):
    target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
    return (target.Label or "").strip(), target


def is_joint_container(obj):
    return obj.TypeId in ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup") or (
        obj.TypeId == "App::DocumentObjectGroup" and obj.Label.lower().startswith("joint"))


def is_joint(obj):
    return obj.TypeId == "App::FeaturePython" and (hasattr(obj, "JointType") or hasattr(obj, "ObjectToGround"))


def leaves(obj, prefix):
    """(subname path, leaf object) of every shape-bearing leaf under an assembly component: the component itself when it
    is a body / feature / link to one, else the shape-bearing members of a rigid sub-assembly (App::Part)."""
    target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
    if target.TypeId in ("App::Part", "Assembly::AssemblyObject"):
        out = []
        for child in target.Group:
            if is_joint_container(child) or is_joint(child) or child.TypeId.startswith("App::Origin"):
                continue
            out += leaves(child, prefix + child.Name + ".")
        return out
    if hasattr(target, "Shape") and not target.Shape.isNull():
        return [(prefix, obj)]
    return []


def moving_part(UtilsAssembly, asm, components, ref):
    """The assembly component a joint reference attaches to. FreeCAD 1.1's getMovingPart returns the reference's object,
    which is the component only for references rooted at it ((P01, ["Face1"])); a reference rooted at the assembly with
    the component in its subname ((Assembly, ["P01.Face1"]), the form FreeCAD's own joint tools write) is resolved
    through getComponentReference. Its global joint frame is the same either way (getJcsGlobalPlc)."""
    part = UtilsAssembly.getMovingPart(ref)
    if part is not None and part.Name in components:
        return part
    sub = ref[1][0] if ref is not None and len(ref) == 2 and ref[1] else ""
    comp, _rel = UtilsAssembly.getComponentReference(asm, ref[0], sub) if ref is not None and len(ref) == 2 else (None, "")
    return comp if comp is not None else part


def extract(fcstd, servo_step, out_dir):
    model = {"format": "fcstd-extract/1", "errors": [], "components": [], "joints": [], "grounded": []}
    os.makedirs(os.path.join(out_dir, "shapes"), exist_ok=True)
    try:
        doc = App.openDocument(fcstd)
    except Exception as exc:  # noqa: BLE001
        model["errors"].append("the document does not open: %s" % exc)
        return model
    model["freecad"] = ".".join(App.Version()[:3])
    doc.recompute()
    assemblies = [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"]
    model["n_assemblies"] = len(assemblies)
    if not assemblies:
        model["errors"].append("no Assembly in the document")
        return model
    asm = assemblies[0]
    if len(assemblies) > 1:  # the top-level one: not contained in another assembly
        tops = [a for a in assemblies if not any(p[0].TypeId == "Assembly::AssemblyObject" for p in (a.Parents or []))]
        asm = tops[0] if tops else asm
    root, root_sub = asm, ""
    parents = asm.Parents or []
    if parents:
        root, root_sub = parents[0][0], parents[0][1]
    model["assembly"] = asm.Name

    import UtilsAssembly  # FreeCAD's Assembly helpers (headless-safe)

    servo_ref = Part.read(servo_step)
    servo_vol, servo_ext = servo_ref.Volume, extents(servo_ref)
    shape_id = 0
    for comp in asm.Group:
        if is_joint_container(comp) or is_joint(comp) or comp.TypeId.startswith("App::Origin"):
            continue
        entry = {"name": comp.Name, "label": comp.Label, "type": comp.TypeId, "leaves": []}
        for sub, leaf in leaves(comp, ""):
            label, target = leaf_label(leaf)
            path = root_sub + comp.Name + "." + sub
            rec = {"label": label, "type": target.TypeId, "path": path}
            try:
                shp = Part.getShape(root, path, needSubElement=False, transform=True).copy()
                rec["valid"] = bool(shp.isValid())
                rec["n_solids"] = len(shp.Solids)
                rec["volume"] = float(shp.Volume)
                rec["placement"] = matrix(shp.Placement)
                local = shp.copy()
                local.Placement = App.Placement()
                rec["is_servo"] = bool(abs(local.Volume - servo_vol) <= SERVO_VOLUME_REL_TOL * servo_vol and all(
                    abs(a - b) <= SERVO_EXTENT_TOL_MM for a, b in zip(extents(local), servo_ext)))
                shape_id += 1
                rec["step"] = "shapes/%03d.step" % shape_id
                shp.exportStep(os.path.join(out_dir, rec["step"]))
                # the exact shape beside it: a STEP round trip can turn a valid solid invalid (wire nesting re-read)
                rec["brep"] = "shapes/%03d.brep" % shape_id
                shp.exportBrep(os.path.join(out_dir, rec["brep"]))
            except Exception as exc:  # noqa: BLE001
                rec["error"] = "%s" % exc
            entry["leaves"].append(rec)
        model["components"].append(entry)

    components = set(c["name"] for c in model["components"])
    for obj in doc.Objects:
        if not is_joint(obj):
            continue
        if hasattr(obj, "ObjectToGround"):
            tgt = obj.ObjectToGround
            model["grounded"].append(tgt.Name if tgt is not None else None)
            continue
        rec = {"name": obj.Name, "label": obj.Label, "type": str(obj.JointType),
               "suppressed": bool(getattr(obj, "Suppressed", False))}
        try:
            for k in ("1", "2"):
                ref = getattr(obj, "Reference" + k)
                part = moving_part(UtilsAssembly, asm, components, ref)
                rec["part" + k] = part.Name if part is not None else None
                rec["jcs" + k] = matrix(UtilsAssembly.getJcsGlobalPlc(getattr(obj, "Placement" + k), ref))
            for k in ("Min", "Max"):
                rec["enable_angle_" + k.lower()] = bool(getattr(obj, "EnableAngle" + k, False))
                q = getattr(obj, "Angle" + k, 0.0)
                rec["angle_" + k.lower() + "_deg"] = float(getattr(q, "Value", q))
        except Exception as exc:  # noqa: BLE001
            rec["error"] = "%s" % exc
        model["joints"].append(rec)
    return model


def main(argv):
    fcstd, servo_step, out_dir = argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    try:
        model = extract(fcstd, servo_step, out_dir)
    except Exception:  # noqa: BLE001
        model = {"format": "fcstd-extract/1", "errors": ["extraction failed: " + traceback.format_exc()[-1500:]]}
    with open(os.path.join(out_dir, "model.json"), "w") as fh:
        json.dump(model, fh, indent=1, sort_keys=True)
    print("FCSTD-EXTRACT done", len(model.get("components", [])), "components", len(model.get("joints", [])), "joints",
          len(model.get("errors", [])), "errors")


main(sys.argv)
