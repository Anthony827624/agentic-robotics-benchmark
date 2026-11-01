"""The onboard camera's renderer: MuJoCo 3.16's Filament renderer (physically based) on Vulkan, lit by an image light
distilled from an OVRTX path-traced render of the warehouse hall the course stands in, plus the scene's sun.
The hall's window glass is frosted: in the path-traced render the sky behind it shines through; here each pane
glows by the way it faces (the part of the sky behind it), its texture the glass's transmission.

The hall's scenery is render-only (no collision, no effect on the physics): a room bundle of the Unity pack
"Warehouse - Abandoned Factory District 1.1" (meshes per material, base-colour textures) under RENDER_DIR, added to
the model by `add_scenery`. Filament runs in one dedicated thread per process (its engine is thread-bound); MuJoCo
3.16's Filament module serves one model's resources at a time, so only the model rendered last is live."""
import atexit
import ctypes
import gc
import json
import math
import os
import queue
import threading

import numpy as np


def _cap_malloc_arenas():
    """Filament's job threads each get a glibc malloc arena, so the resident size grows by gigabytes over many
    episodes. M_ARENA_MAX must be set before any of those threads allocates: at import."""
    try:
        ctypes.CDLL("libc.so.6").mallopt(-8, 2)
    except (OSError, AttributeError):
        pass


def _trim_heap():
    """Hand the heap freed with a model's resources back to the system. Without it the resident size climbs by 0.3-0.5 GB
    a scene, and the grader runs eight scenes in one process under the task's 4 GB."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


_cap_malloc_arenas()
import mujoco  # noqa: E402

RENDER_DIR = os.environ.get("VISUAL_ROBOTICS_RENDER", "/opt/task-assets/payload/render")
IBL_FILE = "ibl.ktx"
SKY_FILE = "skybox.ktx"         # the probe's panorama behind everything (the sky beyond the trimmed scenery); a brighter
                                # one washes the sky white behind the gates' white corner squares
# Calibrated against OVRTX renders of the onboard camera on the recorded oracle flight (per class mean luminance, sunlit
# and shaded apart, each renderer by its own sunlight): the scenery's colour gain; the frosted panes by the way they face
# (e, s: lit by the sun from outside, mostly see-through to the sky map, the probe's panorama of the hall; the others
# opaque, glowing with the sky behind them; u: the roof's).
RGB_GAIN = {k: float(v) for k, v in (kv.split(":") for kv in os.environ.get("VISUAL_ROBOTICS_GAIN", "room:.732").split(","))}
PANES = ("pane_e", "pane_w", "pane_n", "pane_s", "pane_u")          # u: the roof's, seen from below
PANE_ALPHA = dict(zip(PANES, (float(v) for v in os.environ.get("VISUAL_ROBOTICS_PANE_ALPHA", ".2,1,1,.2,1").split(","))))
PANE_GLOW = dict(zip(PANES, (float(v) for v in os.environ.get("VISUAL_ROBOTICS_PANE_GLOW", "0,6.8,.82,.1,9.9").split(","))))
PANE_ALBEDO = .12
SCENERY = "warehouse"
IBL_INTENSITY = float(os.environ.get("VISUAL_ROBOTICS_IBL", "65500"))
CAM_NEAR, CAM_FAR = 0.01, 60.0
CALL_TIMEOUT = 300.0


class RenderError(RuntimeError):
    pass


def add_scenery(spec, rgb_gain=None):
    """Add the hall (RENDER_DIR/warehouse/room.json) to an MjSpec as visual-only world geoms: one mesh per material,
    its base-colour texture marked sRGB (Filament reads MuJoCo's default "auto" colour space as linear)."""
    root = os.path.join(RENDER_DIR, SCENERY)
    with open(os.path.join(root, "room.json")) as fh:
        room = json.load(fh)
    gains = rgb_gain or RGB_GAIN
    for i, m in enumerate(room["meshes"]):
        z = np.load(os.path.join(root, m["file"]))
        rec = room["materials"][m["material"]]
        mesh = spec.add_mesh()
        mesh.name = "scenery_mesh_%d" % i
        mesh.uservert = z["v"].astype(np.float64).ravel()
        if np.abs(z["n"]).sum() > 0:
            mesh.usernormal = z["n"].astype(np.float64).ravel()
        uv = z["uv"].astype(np.float64).copy()
        uv[:, 1] = 1.0 - uv[:, 1]                         # Unity's uv origin is the image's bottom-left corner
        mesh.usertexcoord = uv.ravel()
        mesh.userface = z["f"].astype(np.int32).ravel()
        mesh.inertia = mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL
        mat = spec.add_material()
        mat.name = "scenery_mat_%d" % i
        cls = rec.get("class", "room")
        g = float(gains.get(cls, gains.get("room", 1.0)))
        rgba = list(rec.get("rgba", [1, 1, 1, 1]))
        if cls in PANE_GLOW:
            mat.rgba = [PANE_ALBEDO] * 3 + [PANE_ALPHA[cls]]
            mat.emission = PANE_GLOW[cls]
        else:
            mat.rgba = [min(1.0, rgba[c] * g) for c in range(3)] + [1.0]
        if rec.get("textures", {}).get("rgb"):
            tex = spec.add_texture()
            tex.name = "scenery_tex_%d" % i
            tex.type = mujoco.mjtTexture.mjTEXTURE_2D
            tex.file = os.path.join(root, "textures", rec["textures"]["rgb"])
            tex.colorspace = mujoco.mjtColorSpace.mjCOLORSPACE_SRGB
            texs = list(mat.textures)
            texs[int(mujoco.mjtTextureRole.mjTEXROLE_RGB)] = tex.name
            mat.textures = texs
        geom = spec.worldbody.add_geom()
        geom.name = "scenery_%d" % i
        geom.type = mujoco.mjtGeom.mjGEOM_MESH
        geom.meshname = mesh.name
        geom.material = mat.name
        geom.contype = 0
        geom.conaffinity = 0
        geom.group = 1
    return len(room["meshes"])


class FilamentView(object):
    """One model's camera images; the Filament objects live in the renderer's thread."""

    def __init__(self, model, shown_groups=(0, 1, 2)):
        self._fil = _Filament.instance()
        self._sid = self._fil.call(self._fil.new_session, model, tuple(shown_groups))
        self._closed = False

    def render(self, model, data, camera, width, height):
        cid = model.camera(camera).id
        pos = data.cam_xpos[cid].copy()
        R = data.cam_xmat[cid].reshape(3, 3)
        fovy = float(model.cam_fovy[cid])
        return self._fil.call(self._fil.draw, self._sid, data, pos, -R[:, 2], R[:, 1], fovy, width, height)

    def close(self):
        if not self._closed:
            self._closed = True
            self._fil.call(self._fil.free_session, self._sid)


class _Filament(object):
    _inst = None
    _guard = threading.Lock()

    @classmethod
    def instance(cls):
        with cls._guard:
            if cls._inst is None or cls._inst.pid != os.getpid():
                cls._inst = cls()
            return cls._inst

    def __init__(self):
        self.pid = os.getpid()
        self.jobs = queue.Queue()
        self.models = {}
        self.active = None
        self.live = []
        self.targets = {}
        self.next_sid = 0
        self.thread = threading.Thread(target=self._loop, name="filament", daemon=True)
        self.thread.start()
        self.call(self._start)

    def call(self, fn, *args):
        done = threading.Event()
        out = []
        self.jobs.put((fn, args, done, out))
        if not done.wait(CALL_TIMEOUT):
            raise RenderError("the renderer did not answer within %.0f s" % CALL_TIMEOUT)
        ok, val = out[0]
        if not ok:
            raise val
        return val

    def stop(self, timeout=30.0):
        if self.pid == os.getpid() and self.thread.is_alive():
            self.jobs.put((None, (), None, None))
            self.thread.join(timeout)

    def _loop(self):
        while True:
            fn, args, done, out = self.jobs.get()
            if fn is None:
                self._shutdown()
                return
            try:
                res = (True, fn(*args))
            except BaseException as exc:  # noqa: BLE001 - handed over without its traceback (it holds Filament objects)
                res = (False, RenderError("renderer: %s: %s" % (type(exc).__name__, exc)))
            if done is not None:
                out.append(res)
                done.set()

    def _start(self):
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):
            os.environ.pop(k, None)
        import mujoco._render_filament as F
        from mujoco.experimental.studio import window  # noqa: F401  registers Filament's resource providers
        self.F = F
        path = os.path.join(RENDER_DIR, IBL_FILE)
        if not os.path.isfile(path):
            raise RenderError("render asset missing: " + path)
        self.ctx = F.Context(F.ContextConfig(graphics_api=F.GraphicsApi.GRAPHICS_API_VULKAN))
        self.ctx.set_clear_color([0.0, 0.0, 0.0])
        with open(path, "rb") as fh:
            raw = fh.read()
        tc = F.TextureConfig()
        tc.width, tc.height, tc.format = len(raw), 0, F.PixelFormat.PIXEL_FORMAT_KTX
        tc.sampler_type, tc.color_space = F.SamplerType.TEXTURE_CUBE, F.ColorSpace.COLORSPACE_LINEAR
        self.ibl_tex = self.ctx.create_texture(tc)
        td = F.TextureData()
        td.set_image(raw)
        self.ibl_tex.upload(td)
        self.sky_tex = None
        sky = os.path.join(RENDER_DIR, SKY_FILE)
        if os.path.isfile(sky):
            with open(sky, "rb") as fh:
                raw = fh.read()
            tc = F.TextureConfig()
            tc.width, tc.height, tc.format = len(raw), 0, F.PixelFormat.PIXEL_FORMAT_KTX
            tc.sampler_type, tc.color_space = F.SamplerType.TEXTURE_CUBE, F.ColorSpace.COLORSPACE_LINEAR
            self.sky_tex = self.ctx.create_texture(tc)
            td = F.TextureData()
            td.set_image(raw)
            self.sky_tex.upload(td)

    def new_session(self, model, groups):
        sid = self.next_sid
        self.next_sid += 1
        self.models[sid] = (model, groups)
        return sid

    def free_session(self, sid):
        self.models.pop(sid, None)
        if self.active == sid:
            self._evict()

    def _evict(self):
        while self.live:
            self.live.pop(0)
        gc.collect()
        _trim_heap()
        self.active = None

    def _activate(self, sid):
        if self.active == sid:
            return
        model, groups = self.models[sid]
        self._evict()
        F = self.F
        res = F.ModelResources(self.ctx, model)
        scn = self.ctx.create_scene(F.SceneParams())
        scn.configure_from_model(model)
        lights = F.ModelLights(scn, res)
        rends = F.ModelRenderables(scn, res)
        opt = mujoco.MjvOption()
        opt.geomgroup[:] = 0
        for g in groups:
            opt.geomgroup[g] = 1
        opt.sitegroup[:] = 0
        rends.set_options(opt)
        lp = F.LightParams()
        lp.type, lp.texture, lp.color, lp.intensity = F.LightType.LIGHT_IMAGE, self.ibl_tex, [1.0, 1.0, 1.0], IBL_INTENSITY
        ibl = self.ctx.create_light(lp)
        scn.add_light(ibl)
        if self.sky_tex is not None:
            scn.set_skybox(self.sky_tex)
        self.live = [rends, lights, ibl, scn, res]
        self.active = sid

    def _target(self, width, height):
        F = self.F
        key = (width, height)
        if key not in self.targets:
            buf = np.zeros((height, width, 3), np.uint8)
            tgt = self.ctx.create_render_target(F.RenderTargetConfig(color_format=F.PixelFormat.PIXEL_FORMAT_RGB8))
            tgt.resize(width, height)
            read = F.ReadRequest()
            read.target = tgt
            read.set_buffer(buf)
            self.targets[key] = (tgt, read, buf)
        return self.targets[key]

    def draw(self, sid, data, pos, forward, up, fovy, width, height):
        F = self.F
        self._activate(sid)
        rends, lights, _ibl, scn, _res = self.live
        lights.update(data)
        rends.update(data)
        tgt, read, buf = self._target(width, height)
        top = CAM_NEAR * math.tan(math.radians(fovy) / 2.0)
        cam = F.Camera(pos=np.asarray(pos, np.float32), forward=np.asarray(forward, np.float32),
                       up=np.asarray(up, np.float32), frustum_center=0.0, frustum_width=top * width / height,
                       frustum_bottom=-top, frustum_top=top, frustum_near=CAM_NEAR, frustum_far=CAM_FAR)
        req = F.RenderRequest(camera=cam, draw_mode=F.DrawMode.DRAW_MODE_DEFAULT)
        req.scene, req.target = scn, tgt
        req.viewport.left, req.viewport.bottom, req.viewport.width, req.viewport.height = 0, 0, width, height
        self.ctx.wait_for_frame(self.ctx.render([req], [read]))
        return buf.copy()

    def _shutdown(self):
        self.models.clear()
        self._evict()
        self.targets.clear()
        self.ibl_tex = None
        self.sky_tex = None
        gc.collect()
        self.ctx = None
        gc.collect()


@atexit.register
def _stop_renderer():
    if _Filament._inst is not None:
        _Filament._inst.stop()
