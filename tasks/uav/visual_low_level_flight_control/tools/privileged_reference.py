"""Reviewer-only state-feedback feasibility check; never an agent submission."""
import argparse
import json
import math
import os
from pathlib import Path
import sys

TASK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK/"verify"))
from world import World, MASS, THRUST_MAX
import numpy as np


class PrivilegedController:
    def __init__(self, world):
        self.world = world
        self.targets = [np.r_[world.start[:2], world.gates[0, 2]]]
        self.headings = [world.course_yaw]
        yaw = world.course_yaw
        rotation = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
        transits = {
            1: [(2.65, -1.55), (2.65, .05)],
            2: [(1.0, 1.25), (-.1, 1.15)],
            3: [(-2.7, 2.3), (-2.7, .45)],
        }
        for index, (gate, normal) in enumerate(zip(world.gates, world.gate_normals)):
            heading = math.atan2(normal[1], normal[0])
            for point in transits.get(index, []):
                self.targets.append(np.r_[rotation@point, gate[2]])
                self.headings.append(heading)
            self.targets.extend([gate-.70*normal, gate+.65*normal])
            self.headings.extend([heading]*2)
        for point in ((-1.15, -.75), (-2.7, -.75)):
            self.targets.append(np.r_[rotation@point, world.goal[2]])
            self.headings.append(world.course_yaw-math.pi/2)
        self.targets.append(world.goal.copy())
        self.headings.append(world.course_yaw-math.pi/2)
        self.phase = 0

    def action(self):
        w = self.world
        p = w.position()
        if self.phase < len(self.targets)-1 and np.linalg.norm(p-self.targets[self.phase]) < .12:
            self.phase += 1
        error = self.targets[self.phase]-p
        velocity = w.data.qvel[:3]
        rotation = w.data.xmat[w.agent_id].reshape(3, 3)
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
        acceleration = np.clip(1.6*error[:2]-2.3*velocity[:2], -1.3, 1.3)
        body_acceleration = np.array([[math.cos(yaw), math.sin(yaw)],
                                      [-math.sin(yaw), math.cos(yaw)]])@acceleration
        roll, pitch = -body_acceleration[1]/9.81, body_acceleration[0]/9.81
        thrust = MASS*(9.81+2.2*error[2]-2.3*velocity[2])/max(rotation[2, 2], .8)
        heading = (self.headings[self.phase]+math.pi) % (2*math.pi)-math.pi
        return np.clip([thrust*2/THRUST_MAX-1, roll/.3, pitch/.3, heading/math.pi], -1, 1).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(101, 117)))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    if args.assets:
        os.environ["VISUAL_ROBOTICS_ASSETS"] = str(args.assets.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for seed in args.seeds:
        world = World("flight", seed, render=False)
        controller = PrivilegedController(world)
        actions = []
        try:
            while not world.done:
                action = controller.action()
                world.act(action)
                actions.append({"action": action, "position": world.position().tolist(), **world.metrics()})
            result = {"seed": seed, "privileged": True, **world.metrics()}
            results.append(result)
            (args.out/f"{seed}.json").write_text(json.dumps(actions)+"\n")
            print(json.dumps(result), flush=True)
        finally:
            world.close()
    (args.out/"summary.json").write_text(json.dumps(results, indent=2)+"\n")
    if not all(row["success"] for row in results):
        raise SystemExit("Privileged feasibility check failed")


if __name__ == "__main__":
    main()
