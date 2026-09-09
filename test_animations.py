import math
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import deskbuddy as db
from PySide6.QtGui import QImage


class AnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = db.QApplication.instance() or db.QApplication([])

    def setUp(self):
        self.buddy = db.Buddy(db.QRect(0, 0, 640, 480), 240)

    def settle(self, state):
        self.buddy.state = state
        for _ in range(20):
            self.buddy.animate()

    def test_climbs_from_window_to_overlapping_window_in_both_directions(self):
        for facing in (-1, 1):
            with self.subTest(facing=facing):
                buddy = db.Buddy(db.QRect(0, 0, 640, 480), 440)
                lower = {"x0": 80, "x1": 300, "y": 300, "hwnd": 1}
                upper = {"x0": 302, "x1": 540, "y": 160, "hwnd": 2}
                if facing < 0:
                    lower = {"x0": 302, "x1": 540, "y": 300, "hwnd": 1}
                    upper = {"x0": 80, "x1": 300, "y": 160, "hwnd": 2}
                wall = {"x": upper["x0"] if facing > 0 else upper["x1"],
                        "y0": 160, "y1": 400, "hwnd": 2, "side": -facing}
                buddy.ledge = lower
                buddy.ledges = [lower, upper]
                buddy.walls = [wall]
                buddy.facing = facing
                buddy.anchor_x = buddy.walk_max() if facing > 0 else buddy.walk_min()
                buddy.timer = 1000
                buddy.set_pose(buddy.anchor_x, buddy.ground_y())
                with patch.object(db.random, "random", return_value=0.0):
                    buddy.update(db.QPoint(320, 100))
                self.assertEqual(buddy.state, db.CLIMB)
                self.assertEqual(buddy.facing, facing)
                self.assertEqual(buddy.climb_y, lower["y"])
                self.assertLess((buddy.anchor_x - wall["x"]) * facing, 0)
                for _ in range(100):
                    buddy.update(db.QPoint(320, 100))
                    if buddy.state != db.CLIMB:
                        break
                    self.assertLess((buddy.anchor_x - wall["x"]) * facing, 0)
                self.assertEqual(buddy.state, db.WALK)
                self.assertEqual(buddy.ledge, upper)

    def test_walking_plants_feet_and_lifts_only_the_swinging_foot(self):
        for facing in (-1, 1):
            buddy = self.buddy
            buddy.facing = facing
            buddy.phase = 0.4
            buddy.timer = 1000
            buddy.animate()
            planted_x = buddy.pts[db.FOOT_L].x
            for _ in range(6):
                buddy.update(db.QPoint(320, 100))
                self.assertAlmostEqual(buddy.pts[db.FOOT_L].x, planted_x)
                self.assertEqual(buddy.pts[db.FOOT_L].y, buddy.floor_y)
                self.assertLess(buddy.pts[db.FOOT_R].y, buddy.floor_y)

    def test_climbing_plants_opposite_hand_and_foot_on_wall(self):
        for facing in (-1, 1):
            buddy = self.buddy
            buddy.state = db.CLIMB
            buddy.facing = facing
            buddy.wall = {"x": 300, "y0": 40, "y1": 440,
                          "hwnd": 1, "side": -facing}
            buddy.walls = [buddy.wall]
            buddy.anchor_x = 300 - facing * 3 * db.SCALE
            buddy.climb_y = 300
            buddy.phase = 0.4
            buddy.animate()
            contacts = {joint: buddy.pts[joint].y for joint in (db.FOOT_L, db.HAND_R)}
            for _ in range(6):
                buddy.update(db.QPoint(320, 100))
                for joint, height in contacts.items():
                    self.assertAlmostEqual(buddy.pts[joint].x, 300)
                    self.assertAlmostEqual(buddy.pts[joint].y, height)
                self.assertLess((buddy.pts[db.FOOT_R].x - 300) * facing, 0)

    def test_knees_bend_forward_and_legs_stay_within_reach(self):
        for state in (db.WALK, db.CLIMB):
            self.settle(state)
            for facing in (-1, 1):
                self.buddy.facing = facing
                for frame in range(60):
                    self.buddy.phase = frame * math.tau / 60
                    self.buddy.animate()
                    hips = self.buddy.pts[db.HIPS]
                    for foot_index in (db.FOOT_L, db.FOOT_R):
                        foot = self.buddy.pts[foot_index]
                        self.assertLessEqual(math.hypot(foot.x - hips.x, foot.y - hips.y),
                                             db.LEG_LEN + 0.001)
                        painter = Mock()
                        db.Overlay.limb(self, painter, hips, foot,
                                        db.PALETTE["P"], db.PALETTE["F"], True)
                        knee_x = painter.drawLine.call_args_list[0].args[2]
                        midpoint_x = (hips.x + foot.x) / 2
                        self.assertGreaterEqual((knee_x - midpoint_x) * facing, -1.0)

    def test_sitting_faces_forward_and_kicks_alternately(self):
        self.settle(db.SIT)
        buddy = self.buddy
        heights = []
        for frame in range(int((db.SIT_REST_SECONDS + 2 * db.SIT_SWING_SECONDS) * db.FPS)):
            buddy.phase = frame * 0.06
            buddy.sit_time = frame / db.FPS
            buddy.facing = -1
            buddy.animate()
            left_pose = [(point.x, point.y) for point in buddy.pts]
            buddy.facing = 1
            buddy.animate()
            self.assertEqual(left_pose, [(point.x, point.y) for point in buddy.pts])
            self.assertEqual(buddy.pts[db.HEAD].x, buddy.anchor_x)
            self.assertEqual(buddy.pts[db.CHEST].x, buddy.anchor_x)
            self.assertEqual(buddy.pts[db.HIPS].y, buddy.floor_y - db.SCALE)
            self.assertLess(buddy.pts[db.FOOT_L].x, buddy.anchor_x)
            self.assertGreater(buddy.pts[db.FOOT_R].x, buddy.anchor_x)
            heights.append((buddy.pts[db.FOOT_L].y, buddy.pts[db.FOOT_R].y))
        for side in (0, 1):
            travel = max(pair[side] for pair in heights) - min(pair[side] for pair in heights)
            self.assertGreater(travel, 1.7 * db.SCALE)
            self.assertLessEqual(travel, 1.8 * db.SCALE + 0.001)
        self.assertTrue(any(left < right - db.SCALE for left, right in heights))
        self.assertTrue(any(right < left - db.SCALE for left, right in heights))
        for before, after in zip(heights, heights[1:]):
            self.assertLess(max(abs(after[side] - before[side]) for side in (0, 1)),
                            0.06 * db.SCALE)

    def test_sitting_rests_between_slow_swings(self):
        self.settle(db.SIT)
        buddy = self.buddy
        for cycle in range(2):
            start = cycle * (db.SIT_REST_SECONDS + 2 * db.SIT_SWING_SECONDS)
            for frame in range(int(db.SIT_REST_SECONDS * db.FPS)):
                buddy.sit_time = start + frame / db.FPS
                buddy.animate()
                for foot in (db.FOOT_L, db.FOOT_R):
                    self.assertAlmostEqual(buddy.pts[foot].y, buddy.floor_y + 6.8 * db.SCALE)
            for fraction, lifted in ((0.25, db.FOOT_R), (0.75, db.FOOT_L)):
                buddy.sit_time = start + db.SIT_REST_SECONDS + fraction * db.SIT_SWING_SECONDS
                buddy.animate()
                self.assertAlmostEqual(buddy.pts[lifted].y, buddy.floor_y + 5 * db.SCALE)

    def test_sitting_is_frequent_and_lasts_longer(self):
        self.buddy.allow_perch = False
        for roll in (0.2, 0.6, 0.71):
            with patch.object(db.random, "random", return_value=roll):
                self.buddy.pick_idle()
            self.assertEqual(self.buddy.state, db.SIT)
            self.assertGreaterEqual(self.buddy.timer, 12 * db.FPS)
            self.assertLessEqual(self.buddy.timer, 22 * db.FPS)

    def test_sitting_clock_advances_and_resets_on_entry(self):
        self.settle(db.SIT)
        self.buddy.timer = 1000
        for _ in range(db.FPS):
            self.buddy.update(db.QPoint(320, 100))
        self.assertAlmostEqual(self.buddy.sit_time, 1.0)
        self.settle(db.IDLE)
        self.settle(db.SIT)
        self.assertEqual(self.buddy.sit_time, 0.0)

    def test_settled_transitions_ease_and_finish(self):
        buddy = self.buddy
        for state in (db.SIT, db.IDLE, db.SLEEP, db.WALK):
            start_y = buddy.pts[db.HIPS].y
            buddy.state = state
            buddy.animate()
            self.assertIsNotNone(buddy.pose_from)
            self.assertLess(abs(buddy.pts[db.HIPS].y - start_y), db.SCALE)
            self.settle(state)
            self.assertIsNone(buddy.pose_from)
            positions = [(point.x, point.y) for point in buddy.pts]
            buddy.animate()
            self.assertEqual(positions, [(point.x, point.y) for point in buddy.pts])

    def test_idle_has_separate_feet_and_hands(self):
        self.settle(db.IDLE)
        for left, right in ((db.FOOT_L, db.FOOT_R), (db.HAND_L, db.HAND_R)):
            self.assertLess(self.buddy.pts[left].x, self.buddy.anchor_x)
            self.assertGreater(self.buddy.pts[right].x, self.buddy.anchor_x)

    def test_throw_cancels_pose_blending(self):
        self.buddy.state = db.SIT
        self.buddy.animate()
        self.buddy.go_free(4, -8)
        self.assertIsNone(self.buddy.pose_from)
        self.assertEqual(self.buddy.state, db.FREE)
        for point in self.buddy.pts:
            self.assertAlmostEqual(point.vx, 4, delta=0.61)
            self.assertAlmostEqual(point.vy, -8, delta=0.61)

    def test_seated_pose_follows_ledge(self):
        self.buddy.ledge = {"x0": 80, "x1": 560, "y": 180, "hwnd": 123}
        self.settle(db.SIT)
        before = [point.y for point in self.buddy.pts]
        self.buddy.ledge["y"] += 30
        self.buddy.animate()
        for point, old_y in zip(self.buddy.pts, before):
            self.assertAlmostEqual(point.y - old_y, 30)

    def test_all_kinematic_states_render(self):
        class Renderer:
            draw_buddy = db.Overlay.draw_buddy
            seated_leg = db.Overlay.seated_leg
            arm = db.Overlay.arm
            limb = db.Overlay.limb
            part = db.Overlay.part
            draw_head = db.Overlay.draw_head
            zzz = db.Overlay.zzz

        renderer = Renderer()
        renderer.buddy = self.buddy
        renderer.head_pm = db.build_pixmap(db.HEAD_ART)
        renderer.torso_pm = db.build_pixmap(db.TORSO_ART)
        for state in (db.WALK, db.IDLE, db.SIT, db.SLEEP, db.CLIMB):
            self.settle(state)
            self.buddy.climb_y = 240
            for frame in range(12):
                self.buddy.phase = frame * math.pi / 6
                self.buddy.sit_time = frame
                self.buddy.animate()
                image = QImage(640, 480, QImage.Format_ARGB32)
                image.fill(db.Qt.transparent)
                painter = db.QPainter(image)
                try:
                    renderer.draw_buddy(painter)
                finally:
                    painter.end()
                head = self.buddy.pts[db.HEAD]
                self.assertGreater(image.pixelColor(int(head.x), int(head.y)).alpha(), 0)


if __name__ == "__main__":
    unittest.main()