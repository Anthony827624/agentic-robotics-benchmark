"""Private Crazyflie scene, dynamics and authoritative success conditions."""
import base64
import io
import json
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zlib

os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
os.environ.setdefault("LP_NUM_THREADS", "2")
import fil_render
import mujoco
import numpy as np
from PIL import Image

SIZE = 512
DT = .2
LIMITS = {"flight": 300}
MASS = .027
THRUST_MAX = .35
GATE_HALF = .35
# Scene randomization (course frame: the nominal switchback below, rotated about the hall centre).
NOMINAL_GATES = np.array([[-1.4, -1.5], [1.65, .05], [-.1, 1.9], [-1.9, .45]])
NOMINAL_HEADINGS = np.array([0., np.pi, np.pi/2, 0.])
NOMINAL_START = np.array([-2.9, -1.5])
NOMINAL_GOAL = np.array([-2.8, -2.3])
COURSE_TURN = .18        # rad around a random multiple of 90 degrees
GATE_SHIFT = .25         # m, each gate centre
GATE_TURN = .2           # rad, each gate's heading
START_SHIFT = .25        # m
START_TURN = math.pi/2   # rad, the drone's initial heading relative to the course
GOAL_SHIFT = .15         # m
CABINET_HALF = .6
CLEARANCE = .25          # m from the cabinet to any gate's frame, approach lane or exit lane (nominal: .30)
CAMERA_FOV = 100.
CAMERA_TILT = math.radians(8)
GATE_COLORS = ((1., .72, .04, 1.), (.04, .78, .18, 1.),
               (.85, .04, .85, 1.), (.04, .85, .95, 1.))
# The onboard camera's look (render only): the course stands on a test pad in a hall of an abandoned factory (the
# render scenery, fil_render.py), lit by the hall's image light and the sun.
PAD = (4.35, 4.5, .03)                   # half sizes; the pad's top is the floor (z = 0)
SUN_DIR = (-.383, .663, -.643)          # the sun at 300 degrees azimuth, 40 degrees up (the OVRTX demo's)
SUN_COLOR = (1., .82, .62)
SUN_LUX = float(os.environ.get("VISUAL_ROBOTICS_SUN", "623000"))  # Filament's default exposure: 10x the OVRTX camera's
COURSE_GAIN = {k: float(v) for k, v in (kv.split(":") for kv in
               os.environ.get("VISUAL_ROBOTICS_COURSE_GAIN", "pad_concrete:1,cabinet_rust:1").split(","))}
COURSE_GLOW = {k: float(v) for k, v in (kv.split(":") for kv in
               os.environ.get("VISUAL_ROBOTICS_COURSE_GLOW", "pad_concrete:.0054,cabinet_rust:0").split(","))}
# The course's markings glow a little (fluorescent paint), so that the colours and the white squares the instruction
# names stay what it says under the hall's light and in the cabinet's shadow.
GLOW_COLOR, GLOW_WHITE = .35, 2.0       # white glows bright: a 5 cm corner square seen from 5 m stays white on the dark floor
# the coloured markings at a fraction of their colour, glowing proportionally more: the same glow in the shade, less
# sunlit diffuse to wash a colour out under the low sun
MARK_DIM = float(os.environ.get("VISUAL_ROBOTICS_MARK_DIM", ".5"))
# The course's skins (render only, as in the OVRTX demo): worn painted steel for the gates (each in its paint), dark
# powder-coated steel for the legs, feet and back plates, white retro-reflective tape for the corner squares, a painted
# sign, floor paint worn through in spots for the arrows and the landing disc (textures and decals in RENDER_DIR/course,
# skins.json). The boxes stay as they are for the physics, hidden from the camera (group 3); each wears a visual mesh
# twin. The coloured skins' textures are relative to their paint, which their rgba carries, so that the glow keeps it.
SKINS = os.environ.get("VISUAL_ROBOTICS_SKINS", "1") != "0"
SKIN_TUBE_M = 1.0                       # metres per texture repeat along a tube face (course_textures_fil.py's T)
# the hall's shade is darker than the courtyard's: twice the markings' glow keeps each paint's colour in the shade
SKIN_GLOW = float(os.environ.get("VISUAL_ROBOTICS_SKIN_GLOW", str(2*GLOW_COLOR)))
SKIN_DIM = float(os.environ.get("VISUAL_ROBOTICS_SKIN_DIM", str(MARK_DIM)))
ARROW_GLOW = float(os.environ.get("VISUAL_ROBOTICS_ARROW_GLOW", ".2"))
# the paints in the task's own colours (the demo's darker paints are too dark in the hall's shade and wash out in its sun
# for the colours to stay what the instruction names); the skins' wear, dust and grime on top
SKIN_TASK_COLORS = os.environ.get("VISUAL_ROBOTICS_SKIN_TASK_COLORS", "1") != "0"
DECAL_Z = .0006                         # the floor paint's slab centre over the pad


def png(image):
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", compress_level=1)
    return base64.b64encode(buffer.getvalue()).decode()


def numbers(values):
    return " ".join(f"{float(v):.8g}" for v in values)


def box_mesh(half, tile):
    """vertex / texcoord / face strings of a box mesh (24 vertices, outward faces) whose faces carry planar texture
    coordinates of `tile` metres per texture repeat (MuJoCo box geoms do not reach every renderer with their uv)."""
    hx, hy, hz = half
    faces = [((1, 0, 0), (0, 1, 0), (0, 0, 1), hx, hy, hz), ((-1, 0, 0), (0, -1, 0), (0, 0, 1), hx, hy, hz),
             ((0, 1, 0), (-1, 0, 0), (0, 0, 1), hy, hx, hz), ((0, -1, 0), (1, 0, 0), (0, 0, 1), hy, hx, hz),
             ((0, 0, 1), (1, 0, 0), (0, 1, 0), hz, hx, hy), ((0, 0, -1), (-1, 0, 0), (0, 1, 0), hz, hx, hy)]
    vert, tex, face = [], [], []
    for n, u, v, dn, du, dv in faces:
        n, u, v = np.array(n), np.array(u), np.array(v)
        base = len(vert)
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            vert.append(n*dn+u*a*du+v*b*dv)
            tex.append((a*du/tile, 1-b*dv/tile))
        face += [(base, base+1, base+2), (base, base+2, base+3)]
    return (numbers(np.ravel(vert)), numbers(np.ravel(tex)), " ".join(str(i) for f in face for i in f))


def skin_mesh(half, key):
    """vertex / texcoord / face strings of a box (24 vertices, outward faces) laid out on a skin texture: a face at least
    three times longer than wide is a tube face (s across it in [.01, .49], t along it, SKIN_TUBE_M a repeat, from a
    per-face offset), any other face a plate face (s in [.51, .99], t in [.01, .99] across it)."""
    hx, hy, hz = half
    faces = [((1, 0, 0), (0, 1, 0), (0, 0, 1), hx, hy, hz), ((-1, 0, 0), (0, -1, 0), (0, 0, 1), hx, hy, hz),
             ((0, 1, 0), (-1, 0, 0), (0, 0, 1), hy, hx, hz), ((0, -1, 0), (1, 0, 0), (0, 0, 1), hy, hx, hz),
             ((0, 0, 1), (1, 0, 0), (0, 1, 0), hz, hx, hy), ((0, 0, -1), (-1, 0, 0), (0, 1, 0), hz, hx, hy)]
    rng = np.random.default_rng(zlib.crc32(key.encode()))
    vert, tex, face = [], [], []
    for n, u, v, dn, du, dv in faces:
        n, u, v = np.array(n), np.array(u), np.array(v)
        base, off = len(vert), rng.random()
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            vert.append(n*dn+u*a*du+v*b*dv)
            if du >= 3*dv:
                s, t = .01+.48*(b+1)/2, off+a*du/SKIN_TUBE_M
            elif dv >= 3*du:
                s, t = .01+.48*(a+1)/2, off+b*dv/SKIN_TUBE_M
            else:
                s, t = .51+.48*(a+1)/2, .01+.98*(b+1)/2
            tex.append((s, 1-t))         # MuJoCo's texture origin is the image's top-left corner
        face += [(base, base+1, base+2), (base, base+2, base+3)]
    return (numbers(np.ravel(vert)), numbers(np.ravel(tex)), " ".join(str(i) for f in face for i in f))


def decal_mesh(path):
    """A floor paint decal (course_textures_fil.py): a thin slab, its top the paint with the worn spots cut out."""
    z = np.load(path)
    uv = z["uv"].astype(np.float64)
    return (numbers(z["v"].ravel()), numbers(np.c_[uv[:, 0], 1-uv[:, 1]].ravel()), " ".join(str(int(i)) for i in z["f"].ravel()))


def asset_root():
    path = Path(os.environ.get("VISUAL_ROBOTICS_ASSETS", "/opt/visual_robotics/assets"))
    if not (path/"bitcraze_crazyflie_2/cf2.xml").is_file():
        raise FileNotFoundError("Fetch the pinned image/visual_assets.json before running")
    return path


def crossed_gate(previous, current, center, normal):
    before, after = np.dot(previous-center, normal), np.dot(current-center, normal)
    if not before < 0 <= after:
        return False
    fraction = -before/(after-before)
    crossing = previous + fraction*(current-previous)
    tangent = np.array([-normal[1], normal[0], 0.])
    # Clearance is for the whole nominal rotor span, not just its center.
    return bool(abs(np.dot(crossing-center, tangent)) < GATE_HALF-.09
                and abs(crossing[2]-center[2]) < GATE_HALF-.09)


def layout_clear(xy, directions, course_yaw):
    """Every gate's frame, approach lane (0.85 m before the entry face) and exit lane (0.65 m past it) keeps
    CLEARANCE from the cabinet, gate centres stay 1.2 m apart, and the lanes stay well inside the flight limits."""
    c, s = math.cos(course_yaw), math.sin(course_yaw)
    cabinet = np.array([.05, 0.])
    for i, (center, heading) in enumerate(zip(xy, directions)):
        normal = np.array([math.cos(heading), math.sin(heading)])
        tangent = np.array([-normal[1], normal[0]])
        points = [center+d*normal for d in (-.85, -.6, -.3, .3, .5, .65)]
        points += [center+side*(GATE_HALF+.06)*tangent for side in (-1, 1)]
        for point in points:
            local = np.abs([c*(point[0]-cabinet[0])+s*(point[1]-cabinet[1]),
                            -s*(point[0]-cabinet[0])+c*(point[1]-cabinet[1])])
            if np.linalg.norm(np.maximum(local-CABINET_HALF, 0.)) < CLEARANCE or np.max(np.abs(point)) > 3.4:
                return False
        for other in xy[i+1:]:
            if np.linalg.norm(center-other) < 1.2:
                return False
    return True


def scene(gates, normals, goal, course_yaw):
    source = asset_root()/"bitcraze_crazyflie_2/cf2.xml"
    root = ET.parse(source).getroot()
    root.set("model", "crazyflie_visual_gate_flight")
    root.find("compiler").set("meshdir", str(source.parent/"assets"))
    root.find("option").set("timestep", ".002")
    root.find("option").set("gravity", "0 0 -9.81")
    # The benchmark supplies an attitude servo, not a motor/firmware replica.
    for tag in ("actuator", "sensor", "keyframe"):
        element = root.find(tag)
        if element is not None:
            root.remove(element)
    visual = ET.SubElement(root, "visual")
    ET.SubElement(visual, "global", offwidth="1600", offheight="1000")
    ET.SubElement(visual, "quality", shadowsize="2048")
    ET.SubElement(visual, "headlight", ambient="0 0 0", diffuse="0 0 0", active="0")
    ET.SubElement(root.find("asset"), "material", name="floor_mat", rgba=".52 .55 .57 1", specular=".15")
    ET.SubElement(root.find("asset"), "material", name="pad_mat", rgba=".45 .45 .45 1", roughness=".82", metallic="0")
    # render-only course dressing: a rust-clad equipment cabinet in a rusty steel frame, a concrete test pad
    course = Path(fil_render.RENDER_DIR)/"course"
    # the pad wears the courtyard's own floor (the pack's mi_Floor_01_01), at the scan's scale (3.74 m a repeat)
    for name, tex in (("cabinet_rust", "cabinet_rust.png"), ("pad_concrete", "pad_floor.png")):
        ET.SubElement(root.find("asset"), "texture", name=name, type="2d", file=str(course/tex), colorspace="sRGB")
        g = COURSE_GAIN.get(name, 1.0)
        ET.SubElement(root.find("asset"), "material", name=name, texture=name, rgba=numbers((g, g, g, 1)),
                      emission="%g" % COURSE_GLOW.get(name, 0.0), roughness=".9" if name == "pad_concrete" else ".75",
                      metallic="0")
    ET.SubElement(root.find("asset"), "material", name="beam_rust", rgba=".33 .2 .13 1", roughness=".7", metallic="0")
    for name, half, tile in (("cabinet_skin", (CABINET_HALF, CABINET_HALF, 1.35), 1.2), ("pad_top", (PAD[0], PAD[1], .0005), 3.74)):
        v, t, f = box_mesh(half, tile)
        ET.SubElement(root.find("asset"), "mesh", name=name, vertex=v, texcoord=t, face=f, inertia="shell")
    ET.SubElement(root.find("asset"), "material", name="mark_white", rgba="1 1 1 1", emission=str(GLOW_WHITE))
    ET.SubElement(root.find("asset"), "material", name="mark_blue", rgba=numbers((.025*MARK_DIM, .10*MARK_DIM, MARK_DIM, 1)),
                  emission=str(GLOW_COLOR/MARK_DIM))
    for i, color in enumerate(GATE_COLORS):
        ET.SubElement(root.find("asset"), "material", name=f"mark_gate_{i}", rgba=numbers(tuple(c*MARK_DIM for c in color[:3])+(1,)),
                      emission=str(GLOW_COLOR/MARK_DIM))
    ET.SubElement(root.find("asset"), "mesh", name="direction_head",
                  vertex=".08 -.24 0 .55 0 0 .08 .24 0 .08 -.24 .003 .55 0 .003 .08 .24 .003",
                  face="0 2 1 3 4 5 0 1 4 0 4 3 1 2 5 1 5 4 2 0 3 2 3 5")
    skin_rgba = {}
    if SKINS:
        with open(course/"skins.json") as fh:
            skins = json.load(fh)["classes"]
        for cls, rec in skins.items():
            ET.SubElement(root.find("asset"), "texture", name=cls+"_rgb", type="2d", file=str(course/(cls+"_rgb.png")),
                          colorspace="sRGB")
            ET.SubElement(root.find("asset"), "texture", name=cls+"_orm", type="2d", file=str(course/(cls+"_orm.png")),
                          colorspace="linear")
            if rec["relative"]:          # a coloured paint: dimmed in the diffuse, glowing in proportion
                paint = (GATE_COLORS[int(cls[-1])][:3] if cls.startswith("skin_gate") else (.025, .10, 1.)) \
                    if SKIN_TASK_COLORS else rec["rgba"]
                rgba, glow = [c*SKIN_DIM for c in paint], SKIN_GLOW/SKIN_DIM
            else:
                rgba, glow = rec["rgba"], GLOW_WHITE if cls == "skin_tape" else ARROW_GLOW if cls == "paint_white" else 0.
            skin_rgba[cls] = numbers(list(rgba)+[1])
            material = ET.SubElement(root.find("asset"), "material", name=cls, rgba=skin_rgba[cls], emission="%g" % glow)
            ET.SubElement(material, "layer", texture=cls+"_rgb", role="rgb")
            if glow == 0:                # this renderer's glow turns white on a material given a roughness, a
                material.set("roughness", "%g" % rec.get("roughness", .5))      # metallic or an ORM texture
                material.set("metallic", "0")
                ET.SubElement(material, "layer", texture=cls+"_orm", role="orm")
        for decal in ("paint_disc", "paint_arrow_tail", "paint_arrow_head"):
            v, t, f = decal_mesh(course/(decal+".npz"))
            ET.SubElement(root.find("asset"), "mesh", name=decal, vertex=v, texcoord=t, face=f, inertia="shell")
    world = root.find("worldbody")
    body = world.find("body")
    body.set("name", "agent")
    body.set("pos", "0 0 0")
    for camera in list(body.findall("camera")):
        body.remove(camera)
    ET.SubElement(body, "camera", name="onboard", pos=".06 0 .015",
                  xyaxes=numbers((0, -1, 0, math.sin(CAMERA_TILT), 0, math.cos(CAMERA_TILT))),
                  fovy=str(CAMERA_FOV))
    ET.SubElement(world, "light", name="sun", type="directional", pos="0 0 10", dir=numbers(SUN_DIR),
                  diffuse=numbers(SUN_COLOR), castshadow="true")
    # The floor plane and the plain hall below stay for the physics; the camera sees the test pad and the scenery.
    ET.SubElement(world, "geom", name="floor", type="plane", size="8 8 .1", material="floor_mat", group="3")
    ET.SubElement(world, "geom", name="test_pad", type="box", pos=numbers((0, .05, -PAD[2]-.001)), size=numbers(PAD),
                  material="pad_mat", contype="0", conaffinity="0")
    ET.SubElement(world, "geom", name="test_pad_top", type="mesh", mesh="pad_top", pos=numbers((0, .05, -.0005)),
                  material="pad_concrete", contype="0", conaffinity="0")

    def box(name, pos, size, color, collision=True, parent=None, material=None, skin=None):
        if material and material.startswith(("mark_gate_", "mark_blue")):        # a geom's rgba overrides its material's
            color = tuple(c*MARK_DIM for c in color[:3]) + (1,)
        geom = ET.SubElement(world if parent is None else parent, "geom", name=name, type="box", pos=numbers(pos),
                             size=numbers(size), rgba=numbers(color), contype=str(int(collision)),
                             conaffinity=str(int(collision)))
        if material:
            geom.set("material", material)
        if skin and SKINS:
            geom.set("group", "3")
            v, t, f = skin_mesh(size, name)
            ET.SubElement(root.find("asset"), "mesh", name=name+"_skin", vertex=v, texcoord=t, face=f, inertia="shell")
            ET.SubElement(world if parent is None else parent, "geom", name=name+"_skin", type="mesh", mesh=name+"_skin",
                          pos=numbers(pos), material=skin, rgba=skin_rgba[skin], contype="0", conaffinity="0")

    def decal(name, mesh, pos, parent, material, hide):
        if SKINS:
            hide.set("group", "3")
            ET.SubElement(parent, "geom", name=name, type="mesh", mesh=mesh, pos=numbers(pos), material=material,
                          rgba=skin_rgba[material], contype="0", conaffinity="0")

    # This opaque, collidable cabinet hides later gates; it is not a scoring penalty.
    obstacle = ET.SubElement(world, "body", name="occluder", pos=".05 0 0",
                             quat=numbers((math.cos(course_yaw/2), 0, 0, math.sin(course_yaw/2))))
    box("occluder_core", (0, 0, 1.35), (CABINET_HALF, CABINET_HALF, 1.35), (.31, .35, .38, 1), parent=obstacle)
    obstacle.find("geom[@name='occluder_core']").set("group", "3")          # the collision box; the camera sees:
    ET.SubElement(obstacle, "geom", name="cabinet_skin", type="mesh", mesh="cabinet_skin", pos="0 0 1.35",
                  material="cabinet_rust", contype="0", conaffinity="0")
    for sx in (-1, 1):
        for sy in (-1, 1):                       # the frame's corner posts: galvanised on the front face, rusty behind
            post = ET.SubElement(obstacle, "geom", name=f"cabinet_post_{sx}_{sy}", type="box",
                                 pos=numbers((sx*CABINET_HALF, sy*CABINET_HALF, 1.36)), size=".035 .035 1.36",
                                 contype="0", conaffinity="0")
            if sy < 0:
                post.set("rgba", ".61 .65 .67 1")
            else:
                post.set("material", "beam_rust")
    for k, z in enumerate((.05, 1.35, 2.68)):          # three rings of rusty beams
        for sx, sy, hx, hy in ((0, 1, CABINET_HALF, .025), (0, -1, CABINET_HALF, .025), (1, 0, .025, CABINET_HALF),
                               (-1, 0, .025, CABINET_HALF)):
            ET.SubElement(obstacle, "geom", name=f"cabinet_beam_{k}_{sx}_{sy}", type="box",
                          pos=numbers((sx*(CABINET_HALF+.012), sy*(CABINET_HALF+.012), z)),
                          size=numbers((hx, hy, .04)), material="beam_rust", contype="0", conaffinity="0")
    for side in (-1, 1):
        box(f"cabinet_edge_{side}", (side*.59, -.61, 1.35), (.015, .012, 1.35),
            (.61, .65, .67, 1), False, obstacle)

    for i, (x, y, z) in enumerate(gates):
        color = GATE_COLORS[i]
        yaw = math.atan2(normals[i, 1], normals[i, 0])
        gate = ET.SubElement(world, "body", name=f"gate_{i}", pos=numbers((x, y, 0)),
                             quat=numbers((math.cos(yaw/2), 0, 0, math.sin(yaw/2))))
        for side in (-1, 1):
            box(f"gate_{i}_post_{side}", (0, side*(GATE_HALF+.03), z), (.035, .03, GATE_HALF+.06), color, parent=gate,
                material=f"mark_gate_{i}", skin=f"skin_gate{i}")
            box(f"gate_{i}_bar_{side}", (0, 0, z+side*(GATE_HALF+.03)), (.035, GATE_HALF, .03), color, parent=gate,
                material=f"mark_gate_{i}", skin=f"skin_gate{i}")
            box(f"gate_{i}_leg_{side}", (0, side*(GATE_HALF+.03), (z-GATE_HALF)/2),
                (.022, .022, (z-GATE_HALF)/2), (.17, .2, .22, 1), parent=gate, skin="skin_leg")
            box(f"gate_{i}_foot_{side}", (0, side*(GATE_HALF+.03), .025), (.18, .10, .025), (.17, .2, .22, 1), parent=gate,
                skin="skin_foot")
        box(f"gate_{i}_arrow_tail", (-.20, 0, .004), (.30, .035, .003), (1, 1, 1, 1), False, gate, "mark_white")
        decal(f"gate_{i}_arrow_tail_paint", "paint_arrow_tail", (-.20, 0, DECAL_Z), gate, "paint_white",
              gate.find(f"geom[@name='gate_{i}_arrow_tail']"))
        head = ET.SubElement(gate, "geom", name=f"gate_{i}_arrow_head", type="mesh", mesh="direction_head",
                             pos="0 0 .004", rgba="1 1 1 1", contype="0", conaffinity="0", material="mark_white")
        decal(f"gate_{i}_arrow_head_paint", "paint_arrow_head", (0, 0, DECAL_Z), gate, "paint_white", head)
        for side in (-1, 1):
            # The neutral back face distinguishes reverse approaches in first person.
            box(f"gate_{i}_back_post_{side}", (.037, side*(GATE_HALF+.03), z), (.002, .03, GATE_HALF+.06),
                (.15, .17, .18, 1), False, gate, skin="skin_back")
            box(f"gate_{i}_back_bar_{side}", (.037, 0, z+side*(GATE_HALF+.03)), (.002, GATE_HALF, .03),
                (.15, .17, .18, 1), False, gate, skin="skin_back")
            for vertical in (-1, 1):
                box(f"gate_{i}_entry_corner_{side}_{vertical}", (-.039, side*(GATE_HALF+.03), z+vertical*(GATE_HALF+.03)),
                    (.002, .025, .025), (1, 1, 1, 1), False, gate, "mark_white", skin="skin_tape")
    goal_disc = ET.SubElement(world, "geom", name="goal", type="cylinder", pos=numbers([*goal[:2], .008]),
                              size=".22 .008", rgba=numbers((.025*MARK_DIM, .10*MARK_DIM, MARK_DIM, 1)), contype="0",
                              conaffinity="0", material="mark_blue")
    decal("goal_paint", "paint_disc", (*goal[:2], DECAL_Z), world, "paint_blue", goal_disc)
    south = np.array([math.sin(course_yaw), -math.cos(course_yaw), 0.])
    sign_yaw = course_yaw-math.pi/2
    sign = ET.SubElement(world, "body", name="goal_beacon", pos=numbers(goal+.65*south),
                         quat=numbers((math.cos(sign_yaw/2), 0, 0, math.sin(sign_yaw/2))))
    box("goal_panel", (0, 0, 0), (.015, .30, .30), (.025, .10, 1, 1), parent=sign, material="mark_blue",
        skin="skin_sign")
    box("goal_panel_leg", (.015, 0, -goal[2]/2), (.02, .02, goal[2]/2),
        (.17, .2, .22, 1), parent=sign, skin="skin_leg")
    for horizontal in (-1, 1):
        for vertical in (-1, 1):
            box(f"goal_corner_{horizontal}_{vertical}", (-.018, horizontal*.27, vertical*.27),
                (.002, .02, .02), (1, 1, 1, 1), False, sign, "mark_white", skin="skin_tape")
    for x in (-4.2, 4.2):
        box(f"hall_side{x}", (x, .5, 1.7), (.10, 4, 1.7), (.42, .46, .49, 1))
    box("hall_back", (0, 4.3, 1.7), (4.3, .10, 1.7), (.37, .41, .43, 1))
    for i, x in enumerate((-3, -1.5, 0, 1.5, 3)):
        box(f"window{i}", (x, 4.18, 2.25), (.48, .015, .55), (.16, .23, .26, 1), False)
    for geom in world.findall("geom"):
        if geom.get("name", "").startswith(("hall_", "window")):
            geom.set("group", "3")
    return ET.tostring(root, encoding="unicode")


def model_with_scenery(xml):
    """The scene's model with the render scenery added (visual geoms only: no contact, no mass, no effect on the
    physics) and the sun at its intensity in lux."""
    spec = mujoco.MjSpec.from_string(xml)
    fil_render.add_scenery(spec)
    model = spec.compile()
    model.light_intensity[model.light("sun").id] = SUN_LUX
    return model


class World:
    def __init__(self, kind, seed, render=True):
        if kind != "flight":
            raise ValueError("this task only implements flight")
        self.kind, self.seed = kind, int(seed)
        rng = np.random.default_rng(self.seed)
        yaw = self.course_yaw = int(rng.integers(4))*math.pi/2+rng.uniform(-COURSE_TURN, COURSE_TURN)
        rotation = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
        for _ in range(1000):
            xy = (NOMINAL_GATES+rng.uniform(-GATE_SHIFT, GATE_SHIFT, (4, 2)))@rotation.T
            directions = NOMINAL_HEADINGS+yaw+rng.uniform(-GATE_TURN, GATE_TURN, 4)
            if layout_clear(xy, directions, yaw):
                break
        else:
            raise RuntimeError("no clear layout")
        heights = rng.uniform([1.05, 1.75, .85, 1.45], [1.35, 2.05, 1.15, 1.75])
        self.gates = np.column_stack((xy, heights))
        self.gate_normals = np.column_stack((np.cos(directions), np.sin(directions), np.zeros(4)))
        self.start = np.r_[(NOMINAL_START+rng.uniform(-START_SHIFT, START_SHIFT, 2))@rotation.T, .055]
        self.start_yaw = yaw+rng.uniform(-START_TURN, START_TURN)
        self.goal = np.r_[(NOMINAL_GOAL+rng.uniform(-GOAL_SHIFT, GOAL_SHIFT, 2))@rotation.T, 1.3]
        self.steps = self.collisions = self.damage = self.phase = self.frame_id = 0
        self.passed_gates = self.hover_ticks = 0
        self.done = self.success = self.airborne = False
        self.failure_reason = None
        self._contacts = set()
        xml = scene(self.gates, self.gate_normals, self.goal, yaw)
        self.model = model_with_scenery(xml) if render else mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        self.agent_id = self.model.body("agent").id
        self.data.qpos[:3] = self.start
        self.data.qpos[3:7] = [math.cos(self.start_yaw/2), 0, 0, math.sin(self.start_yaw/2)]
        mujoco.mj_forward(self.model, self.data)
        self.renderer = fil_render.FilamentView(self.model) if render else None

    def position(self):
        return self.data.xpos[self.agent_id].copy()

    def images(self):
        return [self.renderer.render(self.model, self.data, camera, SIZE, SIZE) for camera in ("onboard",)]

    def observe(self):
        self.frame_id += 1
        return {"images": [png(im) for im in self.images()], "frame_id": self.frame_id,
                "phase": 0, "done": bool(self.done), "completed_stages": [],
                "steps_remaining": max(0, LIMITS["flight"]-self.steps), "damage_remaining": 0}

    def act(self, action):
        if self.done:
            raise ValueError("episode finished")
        if (not isinstance(action, list) or len(action) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                    not math.isfinite(v) or abs(v) > 1 for v in action)):
            raise ValueError("four finite numeric actions in [-1, 1] required")
        self.steps += 1
        desired = np.array(action[1:])*[.3, .3, math.pi]
        for _ in range(round(DT/self.model.opt.timestep)):
            previous = self.position()
            rotation = self.data.xmat[self.agent_id].reshape(3, 3)
            angles = np.array([math.atan2(rotation[2, 1], rotation[2, 2]),
                               math.asin(float(np.clip(-rotation[2, 0], -1, 1))),
                               math.atan2(rotation[1, 0], rotation[0, 0])])
            error = (desired-angles+np.pi) % (2*np.pi)-np.pi
            acceleration = 100*error-20*self.data.qvel[3:6]
            torque = np.clip(self.model.body_inertia[self.agent_id]*acceleration, -.002, .002)
            self.data.xfrc_applied[:] = 0
            self.data.xfrc_applied[self.agent_id, :3] = rotation[:, 2]*(action[0]+1)*THRUST_MAX/2
            self.data.xfrc_applied[self.agent_id, 3:] = rotation@torque
            mujoco.mj_step(self.model, self.data)
            p = self.position()
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                raise RuntimeError("nonfinite simulator state")
            self.airborne |= p[2] > .2
            contacts = set()
            for contact in self.data.contact:
                ids = (int(contact.geom1), int(contact.geom2))
                if not any(self.model.geom_bodyid[g] == self.agent_id for g in ids):
                    continue
                if not self.airborne and any(self.model.geom(g).name == "floor" for g in ids):
                    continue
                contacts.add(tuple(sorted(ids)))
            self.collisions += len(contacts-self._contacts)
            self._contacts = contacts
            if self.passed_gates < len(self.gates) and crossed_gate(
                    previous, p, self.gates[self.passed_gates], self.gate_normals[self.passed_gates]):
                self.passed_gates += 1
            stable = (self.passed_gates == len(self.gates) and np.linalg.norm(p-self.goal) < .22
                      and np.linalg.norm(self.data.qvel[:3]) < .25
                      and np.linalg.norm(self.data.qvel[3:6]) < .5 and not self.collisions)
            self.hover_ticks = self.hover_ticks+1 if stable else 0
            self.success = self.hover_ticks*self.model.opt.timestep >= 2.
            outside = np.any(np.abs(p[:2]) > 3.8) or p[2] > 3.5 or p[2] < -.1
            if self.collisions or outside or self.success:
                self.done = True
                self.failure_reason = "collision" if self.collisions else "out_of_bounds" if outside else None
                break
        if self.steps >= LIMITS["flight"]:
            self.done = True
            if not self.success and self.failure_reason is None:
                self.failure_reason = "budget_exhausted"
        return self.observe() if self.renderer is not None else {}

    def advance(self):
        raise ValueError("no phase transition or reset in this task")

    def report(self, frame_id, point):
        raise ValueError("self-reported success is not accepted")

    def metrics(self):
        return {"success": float(self.success), "steps": self.steps, "collisions": self.collisions,
                "damage_cost": 0, "gates_passed": self.passed_gates,
                "gate_completion_fraction": self.passed_gates/len(self.gates),
                "hover_seconds": self.hover_ticks*self.model.opt.timestep,
                "simulation_seconds": float(self.data.time), "failure_reason": self.failure_reason}

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
