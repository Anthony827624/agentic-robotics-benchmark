"""CPU reviewer media; explicitly distinguish image-only and privileged references."""
import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

TASK = Path(__file__).resolve().parents[1]
os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
os.environ.setdefault("LP_NUM_THREADS", "2")
sys.path[:0] = [str(TASK/"verify"), str(TASK/"oracle")]

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from world import World
from privileged_reference import PrivilegedController


def decode(obs):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(obs["images"][0]))))


def digest():
    result = hashlib.sha256()
    for path in sorted(TASK.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            result.update(str(path.relative_to(TASK)).encode()+b"\0"+path.read_bytes()+b"\0")
    return result.hexdigest()


class Camera:
    def __init__(self, world, width=1280, height=800, topdown=False):
        self.world = world
        self.renderer = mujoco.Renderer(world.model, height, width)
        self.camera = mujoco.MjvCamera()
        self.camera.lookat[:] = [0, 0, .8]
        self.camera.distance, self.camera.azimuth, self.camera.elevation = 7.8, 120, -43
        self.topdown = topdown
        if topdown:
            self.camera.lookat[:] = [0, 0, 0]
            self.camera.distance, self.camera.azimuth, self.camera.elevation = 12, 90, -90
            self.camera.orthographic = 1

    def frame(self):
        option = mujoco.MjvOption()
        option.geomgroup[3] = 0
        previous = self.world.model.vis.global_.fovy
        if self.topdown:
            self.world.model.vis.global_.fovy = math.degrees(2*math.atan(4/self.camera.distance))
        self.renderer.update_scene(self.world.data, camera=self.camera, scene_option=option)
        self.world.model.vis.global_.fovy = previous
        return Image.fromarray(self.renderer.render().copy())

    def close(self):
        self.renderer.close()


def poster(world, obs, output, mode):
    camera = Camera(world)
    scene = camera.frame()
    scene.save(output/"scene.png")
    detail = Camera(world, 400, 280)
    detail.camera.lookat[:] = world.position()
    detail.camera.distance, detail.camera.azimuth, detail.camera.elevation = .32, 135, -30
    closeup = detail.frame()
    closeup.save(output/"crazyflie-detail.png")
    detail.close()
    canvas = Image.new("RGB", (1280, 1040), "#161a1d")
    canvas.paste(scene, (0, 120))
    draw = ImageDraw.Draw(canvas)
    normal = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    draw.text((32, 20), "Crazyflie / Visual Flight Control", font=ImageFont.truetype(bold, 36), fill="#f4f5f6")
    draw.text((32, 74), "Onboard vision > switchbacks and altitude changes > four gates > hover",
              font=ImageFont.truetype(normal, 23), fill="#bdc9cd")
    canvas.paste(closeup, (32, 640))
    draw.rectangle((31, 639, 433, 921), outline="#9caab0", width=2)
    draw.text((32, 607), "Unscaled Crazyflie 2 / same simulation", font=ImageFont.truetype(normal, 18), fill="white")
    top = Image.fromarray(decode(obs)).resize((280, 280))
    canvas.paste(top, (968, 640))
    draw.text((968, 607), "Only agent camera: FPV", font=ImageFont.truetype(normal, 18), fill="white")
    label = "Privileged solvability reference" if mode == "privileged" else "Image-only reference"
    draw.text((32, 946), f"27 g nominal model  |  CPU MuJoCo  |  {label}",
              font=ImageFont.truetype(normal, 22), fill="#dae2e5")
    draw.text((32, 988), "Simulator capture. Custom attitude servo; not a hardware or frontier-model result.",
              font=ImageFont.truetype(normal, 19), fill="#a7b7bd")
    canvas.save(output/"poster.png")
    camera.close()


def route_overview(world, initial_image, records, output):
    """Reviewer-only labels and the measured flight path over an actual camera image."""
    canvas = Image.new("RGB", (960, 1080), "#161a1d")
    canvas.paste(Image.fromarray(initial_image).resize((960, 960)), (0, 80))
    draw = ImageDraw.Draw(canvas)
    normal = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    draw.text((24, 18), "Occluded four-gate switchback course", font=ImageFont.truetype(bold, 30), fill="white")

    def pixel(p):
        return ((p[0]+4)*120, 80+(4-p[1])*120)

    points = [pixel(world.start)]+[pixel(row["position"]) for row in records]
    if len(points) > 1:
        draw.line(points, fill="#263441", width=9)
        draw.line(points, fill="#ff8866", width=4)
    colors = ("#ffc42b", "#46da67", "#ee71eb", "#48e2ed")
    for i, center in enumerate(world.gates):
        x, y = pixel(center)
        x, y = x+34, y-44
        draw.ellipse((x-20, y-20, x+20, y+20), fill=colors[i], outline="#161a1d", width=2)
        draw.text((x, y-1), str(i+1), anchor="mm", font=ImageFont.truetype(bold, 24), fill="#161a1d")
    for label, point in (("START", world.start), ("FINISH", world.goal)):
        x, y = pixel(point)
        draw.text((x-20, y+22), label, font=ImageFont.truetype(bold, 19), fill="#10171c",
                  stroke_width=2, stroke_fill="#e9eff0")
    draw.text((24, 1047), "Reviewer-only overhead capture and measured path. Agent receives only its onboard camera.",
              font=ImageFont.truetype(normal, 17), fill="#c6d1d5")
    canvas.save(output/"route.png")


def run(seed, output, video=False, mode="reference"):
    output.mkdir(parents=True, exist_ok=True)
    world = World("flight", seed)
    controller = None
    if mode == "privileged":
        controller = PrivilegedController(world)
    elif mode == "reference":
        from policy import ControllerSession
        controller = ControllerSession()
    records = []
    writer = camera = None
    if video:
        import imageio.v2 as imageio
        writer = imageio.get_writer(output/"demo.mp4", fps=5, codec="libx264", quality=8, macro_block_size=1)
        camera = Camera(world, 960, 600)
    rng = np.random.default_rng(seed ^ 0x971)
    try:
        obs = world.observe()
        overhead = Camera(world, 960, 960, topdown=True)
        initial_image = np.asarray(overhead.frame())
        overhead.close()
        Image.fromarray(np.concatenate(world.images(), axis=1)).save(output/"initial-agent-view.png")
        while not world.done:
            if mode == "reference":
                action = controller.act(world.steps, obs)
            elif mode == "privileged":
                action = controller.action()
            elif mode == "hover_only":
                action = [.027*9.81/.175-1, 0, 0, 0]
            else:
                action = rng.uniform(-1, 1, 4).tolist()
            obs = world.act(action)
            records.append({"action": action, "frame_id": obs["frame_id"],
                            "position": world.position().tolist(), **world.metrics()})
            if world.steps == 16:
                poster(world, obs, output, mode)
                Image.fromarray(np.concatenate(world.images(), axis=1)).save(output/"agent-view.png")
            if writer is not None:
                frame = camera.frame()
                draw = ImageDraw.Draw(frame)
                frame.paste(Image.fromarray(decode(obs)).resize((240, 240)), (704, 12))
                draw.text((714, 258), "Agent onboard camera", font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17),
                          fill="white", stroke_width=2, stroke_fill="#161a1d")
                draw.rectangle((0, 556, 960, 600), fill="#161a1d")
                draw.text((16, 566), f"{world.data.time:.1f}s  |  Gates: {world.passed_gates}/4  |  Contacts: {world.collisions}  |  Reviewer view",
                          font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18), fill="white")
                writer.append_data(np.asarray(frame))
        Image.fromarray(np.concatenate(world.images(), axis=1)).save(output/"final-agent-view.png")
        route_overview(world, initial_image, records, output)
        result = {"seed": seed, "mode": mode, "privileged": mode == "privileged", "task_sha256": digest(), **world.metrics()}
        (output/"result.json").write_text(json.dumps(result, indent=2)+"\n")
        (output/"actions.json").write_text(json.dumps(records, indent=2)+"\n")
        return result
    finally:
        if writer is not None:
            writer.close()
            camera.close()
        world.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", nargs="+", type=int, default=[101, 102, 103])
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--mode", choices=["reference", "privileged", "hover_only", "random"], default="reference")
    args = parser.parse_args()
    if args.assets:
        os.environ["VISUAL_ROBOTICS_ASSETS"] = str(args.assets.resolve())
    results = []
    for i, seed in enumerate(args.seeds):
        result = run(seed, args.out/str(seed), video=args.video and i == 0, mode=args.mode)
        results.append(result)
        print(json.dumps(result), flush=True)
    (args.out/"summary.json").write_text(json.dumps(results, indent=2)+"\n")
    if args.mode in ("reference", "privileged") and not all(row["success"] == 1 for row in results):
        raise SystemExit("reference failed; do not claim successful validation")


if __name__ == "__main__":
    main()
