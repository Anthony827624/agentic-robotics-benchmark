import base64
import io
from pathlib import Path
import sys
import unittest

TASK = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TASK/"verify"), str(TASK/"oracle")]
import mujoco
import numpy as np
from PIL import Image
from world import World, crossed_gate, LIMITS, MASS, CAMERA_FOV, CAMERA_TILT
from grader import counterfactual_observation


class FlightContract(unittest.TestCase):
    def setUp(self):
        self.world = World("flight", 101, render=False)

    def tearDown(self):
        self.world.close()

    def test_nominal_robot(self):
        w = self.world
        self.assertAlmostEqual(w.model.body_mass[w.agent_id], MASS)
        np.testing.assert_allclose(w.model.body_inertia[w.agent_id], [2.3951e-5, 2.3951e-5, 3.2347e-5])
        self.assertGreaterEqual(sum(w.model.geom_bodyid == w.agent_id), 39)
        np.testing.assert_allclose(w.model.mesh_scale, 1)

    def test_hover_physics(self):
        w = self.world
        w.data.qpos[2] = 1.3
        mujoco.mj_forward(w.model, w.data)
        for _ in range(15):
            w.act([MASS*9.81/.175-1, 0, 0, 0])
        self.assertAlmostEqual(w.position()[2], 1.3, places=4)
        self.assertFalse(w.success)

    def test_invalid_actions_do_not_move(self):
        for action in (None, [], [0]*3, [0]*5, [True, 0, 0, 0], [float("nan"), 0, 0, 0], [2, 0, 0, 0]):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.world.act(action)
        self.assertEqual(self.world.steps, 0)

    def test_directed_aperture(self):
        center = np.array([0., 0., 1.3])
        for yaw in np.linspace(-np.pi, np.pi, 17):
            normal = np.array([np.cos(yaw), np.sin(yaw), 0.])
            tangent = np.array([-normal[1], normal[0], 0.])
            start, end = center-normal, center+normal
            with self.subTest(yaw=yaw):
                self.assertTrue(crossed_gate(start, end, center, normal))
                self.assertFalse(crossed_gate(end, start, center, normal))
                self.assertFalse(crossed_gate(start, start, center, normal))
                self.assertTrue(crossed_gate(start+.25*tangent, end+.25*tangent, center, normal))
                self.assertFalse(crossed_gate(start+.27*tangent, end+.27*tangent, center, normal))
                self.assertFalse(crossed_gate(start+[0, 0, .27], end+[0, 0, .27], center, normal))

    def cross(self, index, reverse=False):
        w = self.world
        normal = w.gate_normals[index]*(-1 if reverse else 1)
        w.data.qpos[:3] = w.gates[index]-.025*normal
        w.data.qpos[3:7] = [1, 0, 0, 0]
        w.data.qvel[:] = 0
        w.data.qvel[:3] = .5*normal
        mujoco.mj_forward(w.model, w.data)
        w.act([MASS*9.81/.175-1, 0, 0, 0])

    def test_only_next_gate_in_correct_direction_counts(self):
        self.cross(1)
        self.assertEqual(self.world.passed_gates, 0)
        self.cross(0, reverse=True)
        self.assertEqual(self.world.passed_gates, 0)
        for index in range(4):
            self.cross(index)
            self.assertEqual(self.world.passed_gates, index+1)
        self.assertFalse(self.world.success)

    def test_course_requires_turns_and_return_travel(self):
        for seed in range(32):
            w = World("flight", seed, render=False)
            try:
                self.assertEqual(len(w.gates), 4)
                # in the course frame (the whole course turns by a multiple of 90 degrees plus jitter)
                c, s = np.cos(w.course_yaw), np.sin(w.course_yaw)
                local = w.gates[:, :2]@np.array([[c, -s], [s, c]])
                self.assertGreater(np.ptp(local[:, 1]), 2)
                self.assertGreater(local[1, 0]-local[0, 0], 2)
                self.assertLess(local[3, 0]-local[1, 0], -2.5)
                self.assertLess(np.dot(w.gate_normals[0], w.gate_normals[1]), -.9)
                self.assertGreater(np.dot(w.gate_normals[0], w.gate_normals[3]), .9)
                self.assertGreater(w.gates[1, 2]-w.gates[0, 2], .39)
                self.assertGreater(w.gates[1, 2]-w.gates[2, 2], .59)
                self.assertGreater(w.gates[3, 2]-w.gates[2, 2], .29)
                for index in range(4):
                    actual = w.data.xmat[w.model.body(f"gate_{index}").id].reshape(3, 3)[:, 0]
                    np.testing.assert_allclose(actual, w.gate_normals[index], atol=1e-7)
            finally:
                w.close()

    def test_goal_without_gates_is_not_success(self):
        w = self.world
        w.data.qpos[:3] = w.goal
        mujoco.mj_forward(w.model, w.data)
        for _ in range(12):
            w.act([MASS*9.81/.175-1, 0, 0, 0])
        self.assertFalse(w.success)
        self.assertEqual(w.passed_gates, 0)

    def test_partial_progress_is_not_full_success(self):
        self.cross(0)
        self.cross(1)
        metrics = self.world.metrics()
        self.assertEqual(metrics["gates_passed"], 2)
        self.assertEqual(metrics["gate_completion_fraction"], .5)
        self.assertEqual(metrics["success"], 0)

    def test_goal_panel_defines_visible_hover_target(self):
        w = self.world
        panel = w.model.body("goal_beacon").id
        forward = w.data.xmat[panel].reshape(3, 3)[:, 0]
        np.testing.assert_allclose(w.data.xpos[panel]-.65*forward, w.goal, atol=1e-7)

    def test_hover_requires_two_continuous_seconds(self):
        w = self.world
        w.data.qpos[:3] = w.goal
        w.data.qpos[3:7] = [1, 0, 0, 0]
        w.passed_gates = 4
        mujoco.mj_forward(w.model, w.data)
        for _ in range(9):
            w.act([MASS*9.81/.175-1, 0, 0, 0])
        self.assertFalse(w.success)
        w.act([MASS*9.81/.175-1, 0, 0, 0])
        self.assertTrue(w.success)

    def test_budget_and_reset(self):
        w = self.world
        w.steps = LIMITS["flight"]-1
        w.act([0, 0, 0, 0])
        self.assertTrue(w.done)
        with self.assertRaises(ValueError):
            w.act([0, 0, 0, 0])
        with self.assertRaises(ValueError):
            w.advance()
        with self.assertRaises(ValueError):
            w.report(1, [0, 0])

    def test_collision_and_landing_fail(self):
        w = self.world
        w.airborne = True
        w.data.qpos[2] = .015
        mujoco.mj_forward(w.model, w.data)
        w.act([-1, 0, 0, 0])
        self.assertTrue(w.done)
        self.assertGreater(w.collisions, 0)
        self.assertFalse(w.success)

    def test_runtime_and_verifier_world_identical(self):
        self.assertEqual((TASK/"setup/runtime/world.py").read_bytes(), (TASK/"verify/world.py").read_bytes())


class Images(unittest.TestCase):
    def test_only_rigid_onboard_camera(self):
        w = World("flight", 101, render=False)
        try:
            self.assertEqual(w.model.ncam, 1)
            camera = w.model.camera("onboard")
            self.assertEqual(w.model.cam_bodyid[camera.id], w.agent_id)
            self.assertEqual(float(camera.fovy[0]), CAMERA_FOV)
            np.testing.assert_allclose(w.model.cam_pos[camera.id], [.06, 0, .015])
            for yaw in (-2.8, -.5, 0, 1.8, 2.9):
                w.data.qpos[3:7] = [np.cos(yaw/2), 0, 0, np.sin(yaw/2)]
                mujoco.mj_forward(w.model, w.data)
                optical_axis = -w.data.cam_xmat[camera.id].reshape(3, 3)[:, 2]
                np.testing.assert_allclose(optical_axis, [np.cos(yaw)*np.cos(CAMERA_TILT),
                    np.sin(yaw)*np.cos(CAMERA_TILT), -np.sin(CAMERA_TILT)], atol=1e-7)
        finally:
            w.close()

    def test_yaw_command_supports_looking_back(self):
        w = World("flight", 101, render=False)
        try:
            w.data.qpos[2] = 2.9
            mujoco.mj_forward(w.model, w.data)
            for _ in range(12):
                w.act([MASS*9.81/.175-1, 0, 0, .8])
            rotation = w.data.xmat[w.agent_id].reshape(3, 3)
            self.assertAlmostEqual(np.arctan2(rotation[1, 0], rotation[0, 0]), .8*np.pi, delta=.01)
            self.assertFalse(w.done)
        finally:
            w.close()

    def test_entry_face_markings_and_solid_occluder(self):
        w = World("flight", 101, render=False)
        try:
            self.assertEqual(w.model.geom("occluder_core").contype[0], 1)
            for index in range(4):
                for side in (-1, 1):
                    back = w.model.geom(f"gate_{index}_back_post_{side}")
                    self.assertGreater(back.pos[0], 0)
                    for vertical in (-1, 1):
                        corner = w.model.geom(f"gate_{index}_entry_corner_{side}_{vertical}")
                        self.assertLess(corner.pos[0], 0)
                        np.testing.assert_allclose(corner.rgba, 1)
        finally:
            w.close()

    def test_camera_changes_with_motion_not_global_map(self):
        w = World("flight", 101)
        try:
            w.data.qpos[2] = 1.3
            mujoco.mj_forward(w.model, w.data)
            initial = w.images()
            self.assertEqual(len(initial), 1)
            self.assertGreater(initial[0].std(), 20)
            w.data.qpos[3:7] = [0, 0, 0, 1]
            mujoco.mj_forward(w.model, w.data)
            turned = w.images()[0]
            self.assertGreater(np.mean(np.abs(turned.astype(float)-initial[0])), 20)
        finally:
            w.close()

    def test_public_images_and_probe_noninterference(self):
        w = World("flight", 101)
        try:
            obs = w.observe()
            self.assertEqual(len(obs["images"]), 1)
            self.assertEqual(set(obs), {"images", "frame_id", "phase", "done", "completed_stages", "steps_remaining", "damage_remaining"})
            self.assertEqual(Image.open(io.BytesIO(base64.b64decode(obs["images"][0]))).size, (512, 512))
            before = w.data.qpos.copy()
            alternate = counterfactual_observation(w, obs, np.random.default_rng(55))
            self.assertNotEqual(obs["images"], alternate["images"])
            np.testing.assert_array_equal(w.data.qpos, before)
            self.assertEqual(w.steps, 0)
            self.assertEqual(w.frame_id, 1)
            self.assertEqual(w.data.time, 0)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
