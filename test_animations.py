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

    def test_crawl_knees_alternate_while_both_hands_stay_grounded(self):
        buddy = self.buddy
        buddy.crouch = 1.0
        for facing in (-1, 1):
            buddy.facing = facing
            for phase, planted, swinging in (
                (math.pi / 2, (db.FOOT_L, db.HAND_R), (db.FOOT_R, db.HAND_L)),
                (3 * math.pi / 2, (db.FOOT_R, db.HAND_L), (db.FOOT_L, db.HAND_R)),
            ):
                with self.subTest(facing=facing, phase=phase):
                    buddy.phase = phase
                    buddy.animate()
                    for foot_index, hand_index in (planted, swinging):
                        foot = buddy.pts[foot_index]
                        painter = Mock()
                        db.Overlay.limb(self, painter, buddy.pts[db.HIPS], foot,
                                        db.PALETTE["P"], db.PALETTE["F"], True)
                        knee_x, knee_y = painter.drawLine.call_args_list[0].args[2:]
                        hand = buddy.pts[hand_index]
                        self.assertEqual(hand.y, buddy.floor_y)
                        self.assertLessEqual(knee_y, buddy.floor_y)
                        self.assertGreater((knee_x - foot.x) * facing, 4 * db.SCALE)
                        self.assertLess(foot.y, knee_y)

    def test_crouching_only_grounds_hands_when_arms_can_reach(self):
        buddy = self.buddy
        buddy.crouch = 0.5
        for phase in (0.0, math.pi):
            buddy.phase = phase
            buddy.animate()
            for hand_index in (db.HAND_L, db.HAND_R):
                hand = buddy.pts[hand_index]
                chest = buddy.pts[db.CHEST]
                self.assertLessEqual(math.hypot(hand.x - chest.x, hand.y - chest.y),
                                     db.ARM_LEN + 0.001)
        buddy.crouch = 1.0
        buddy.animate()
        for hand_index in (db.HAND_L, db.HAND_R):
            self.assertEqual(buddy.pts[hand_index].y, buddy.floor_y)

    def test_crawling_plants_knee_and_opposite_hand_while_moving(self):
        for facing in (-1, 1):
            for phase, foot_index, hand_index in (
                (0.4, db.FOOT_L, db.HAND_R),
                (math.pi + 0.4, db.FOOT_R, db.HAND_L),
            ):
                with self.subTest(facing=facing, phase=phase):
                    buddy = db.Buddy(db.QRect(0, 0, 640, 480), db.CRAWL_HEIGHT)
                    buddy.facing = facing
                    buddy.crouch = 1.0
                    buddy.phase = phase
                    buddy.timer = 1000
                    buddy.allow_perch = False
                    buddy.animate()
                    planted_foot_x = buddy.pts[foot_index].x
                    planted_hand_x = buddy.pts[hand_index].x
                    self.buddy = buddy
                    for _ in range(6):
                        buddy.update(db.QPoint(320, 100))
                        foot = buddy.pts[foot_index]
                        self.assertAlmostEqual(foot.x, planted_foot_x)
                        self.assertAlmostEqual(buddy.pts[hand_index].x, planted_hand_x)
                        painter = Mock()
                        db.Overlay.limb(self, painter, buddy.pts[db.HIPS], foot,
                                        db.PALETTE["P"], db.PALETTE["F"], True)
                        knee_x, knee_y = painter.drawLine.call_args_list[0].args[2:]
                        self.assertAlmostEqual(knee_x, planted_foot_x + facing * 5 * db.SCALE,
                                               delta=1.0)
                        self.assertEqual(knee_y, buddy.floor_y)

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

    def test_desktop_selection_requires_a_real_normalized_drag(self):
        selection = db.DesktopSelection()
        self.assertIsNone(selection.update(True, db.QPoint(100, 100), True))
        self.assertIsNone(selection.update(True, db.QPoint(110, 110), True))
        rect = selection.update(True, db.QPoint(40, 50), True)
        self.assertLess(rect.left(), 50)
        self.assertLess(rect.top(), 60)
        self.assertGreaterEqual(rect.width(), db.SELECTION_DRAG_MIN)
        self.assertGreaterEqual(rect.height(), db.SELECTION_DRAG_MIN)
        self.assertIsNone(selection.update(False, db.QPoint(40, 50), True))
        self.assertIsNone(selection.update(True, db.QPoint(10, 10), False))

    def test_abduction_only_accepts_settled_buddy_and_drops_on_release(self):
        buddy = self.buddy
        target = db.QPoint(320, 160)
        start_x = buddy.pts[db.HIPS].x
        self.assertTrue(buddy.begin_abduction(target))
        self.assertEqual(buddy.state, db.ABDUCT)
        buddy.update(db.QPoint(0, 0))
        self.assertGreater(buddy.pts[db.HIPS].x, start_x)
        self.assertLess(buddy.pts[db.HIPS].x, target.x())
        slow_step = buddy.pts[db.HIPS].x - start_x
        before_fast_follow = buddy.pts[db.HIPS].x
        buddy.move_abduction(db.QPoint(520, 160))
        buddy.update(db.QPoint(0, 0))
        self.assertGreater(buddy.pts[db.HIPS].x - before_fast_follow, slow_step)
        buddy.move_abduction(target)
        for _ in range(180):
            buddy.update(db.QPoint(0, 0))
        self.assertAlmostEqual(buddy.pts[db.HIPS].x, target.x(), delta=db.SCALE)
        self.assertTrue(buddy.end_abduction())
        self.assertEqual(buddy.state, db.FREE)
        self.assertFalse(buddy.end_abduction())
        buddy.state = db.GRABBED
        self.assertFalse(buddy.begin_abduction(target))

    def test_ship_collects_buddy_then_returns_after_away_delay(self):
        ship = db.AlienShip()
        bounds = db.QRect(0, 0, 640, 480)
        rect = db.QRect(280, 180, 80, 80)
        ship.begin(rect, bounds)
        moved_rect = db.QRect(360, 140, 160, 120)
        ship.follow_selection(moved_rect)
        self.assertEqual(ship.rect, moved_rect)
        self.assertGreater(ship.x, rect.center().x())
        self.assertTrue(self.buddy.begin_abduction(rect.center()))
        lift_target = db.QPoint(int(ship.x),
                                int(ship.pickup_y + db.TORSO_LEN + db.NECK_LEN))
        for _ in range(60):
            self.buddy.move_abduction(lift_target)
            self.buddy.update(db.QPoint(0, 0))
            if ship.can_board(self.buddy):
                break
        self.assertTrue(ship.can_board(self.buddy))
        self.assertTrue(self.buddy.board_ship())
        ship.depart(self.buddy.pts[db.HEAD].x)
        returned = False
        for _ in range(db.SHIP_AWAY_FRAMES + 200):
            returned = ship.update() or returned
            if returned:
                break
        self.assertEqual(returned, "returned")
        self.assertEqual(ship.mode, "unloading")
        self.assertEqual(self.buddy.state, db.ABOARD)

    def test_seated_pose_follows_ledge(self):
        self.buddy.ledge = {"x0": 80, "x1": 560, "y": 180, "hwnd": 123}
        self.settle(db.SIT)
        before = [point.y for point in self.buddy.pts]
        self.buddy.ledge["y"] += 30
        self.buddy.animate()
        for point, old_y in zip(self.buddy.pts, before):
            self.assertAlmostEqual(point.y - old_y, 30)

    def bone_lengths(self, buddy):
        for a, b, rest, stiff in db.STICKS:
            if stiff < 0.9:
                continue
            span = math.hypot(buddy.body[b].x - buddy.body[a].x,
                              buddy.body[b].y - buddy.body[a].y)
            self.assertAlmostEqual(span, rest, delta=rest * 0.3)

    def hip_swing(self, buddy):
        """How far past its limit the worst thigh has swung, in degrees."""
        up, forward = buddy.body_frame()
        down = (-up[0], -up[1])
        worst = 0.0
        for root, joint, _tip, _span, _bulge, ahead, behind, _fold in db.RAG_LIMBS:
            dx = buddy.body[joint].x - buddy.body[root].x
            dy = buddy.body[joint].y - buddy.body[root].y
            angle = math.atan2(dx * forward[0] + dy * forward[1],
                               dx * down[0] + dy * down[1])
            worst = max(worst, math.degrees(max(0.0, angle - ahead,
                                                -behind - angle)))
        return worst

    def test_thrown_ragdoll_holds_together_and_gets_back_up(self):
        for vx, vy, spin in ((13, -9, 0.07), (-18, -4, -0.09), (2, -16, 0.0)):
            with self.subTest(throw=(vx, vy)):
                buddy = self.buddy = db.Buddy(db.QRect(0, 0, 640, 480), 240)
                buddy.ledges = [{"x0": 4, "x1": 636, "y": 240, "hwnd": -1}]
                buddy.ledge = buddy.ledges[0]
                buddy.allow_panic = False
                buddy.go_free(vx, vy, spin)
                for _ in range(400):
                    buddy.update(db.QPoint(620, 20))
                    self.bone_lengths(buddy)
                    for point in buddy.body:
                        self.assertTrue(math.isfinite(point.x))
                        self.assertTrue(math.isfinite(point.y))
                    if buddy.state == db.WALK:
                        break
                else:
                    self.fail(f"never got up, stuck in state {buddy.state}")
                self.assertLessEqual(self.hip_swing(buddy), 8)

    def test_ragdoll_limbs_bend_instead_of_staying_rigid(self):
        buddy = self.buddy
        buddy.ledges = [{"x0": 4, "x1": 636, "y": 240, "hwnd": -1}]
        buddy.ledge = buddy.ledges[0]
        buddy.state = db.GRABBED
        buddy.grab_idx = db.HEAD
        buddy.seed_joints()
        hand = db.QPoint(int(buddy.pts[db.HEAD].x), int(buddy.pts[db.HEAD].y))
        for frame in range(240):
            buddy.update(db.QPoint(hand.x() + int(40 * math.sin(frame * 0.05)),
                                   hand.y() - min(frame, 90)))
            self.bone_lengths(buddy)
        hips = buddy.pts[db.HIPS]
        self.assertLess(buddy.pts[db.HEAD].y, buddy.pts[db.CHEST].y)
        self.assertLess(buddy.pts[db.CHEST].y, hips.y)
        for foot in (db.FOOT_L, db.FOOT_R):
            self.assertGreater(buddy.pts[foot].y, hips.y)
        self.assertLessEqual(self.hip_swing(buddy), 8)
        for root, joint, tip, _span, _bulge, _f, _b, _fold in db.RAG_LIMBS:
            a, mid, end = buddy.body[root], buddy.body[joint], buddy.body[tip]
            cross = ((mid.x - a.x) * (end.y - mid.y) -
                     (mid.y - a.y) * (end.x - mid.x))
            dot = ((mid.x - a.x) * (end.x - mid.x) +
                   (mid.y - a.y) * (end.y - mid.y))
            self.assertGreater(abs(math.degrees(math.atan2(cross, dot))), 1.0)

    def test_hard_landing_does_not_skid_across_the_floor(self):
        buddy = self.buddy = db.Buddy(db.QRect(0, 0, 900, 600), 500)
        buddy.ledges = [{"x0": 4, "x1": 896, "y": 500, "hwnd": -1}]
        buddy.ledge = buddy.ledges[0]
        buddy.allow_panic = False
        buddy.go_free(18, -24, 0.08)
        landed = None
        for frame in range(180):
            buddy.update(db.QPoint(800, 20))
            low = max(point.y for point in buddy.pts)
            if landed is None and low >= buddy.floor_y - 2 * db.SCALE - 0.1:
                landed = (frame, sum(point.x for point in buddy.pts) / len(buddy.pts))
            if landed is not None and frame == landed[0] + 60:
                centre = sum(point.x for point in buddy.pts) / len(buddy.pts)
                self.assertLess(abs(centre - landed[1]), 20 * db.SCALE)
                return
        self.fail("ragdoll did not remain on the floor long enough to measure")

    def test_seeding_the_ragdoll_never_moves_a_posed_point(self):
        buddy = self.buddy
        for state in (db.WALK, db.IDLE, db.SIT, db.SLEEP, db.CLIMB):
            with self.subTest(state=state):
                self.settle(state)
                posed = [(point.x, point.y) for point in buddy.pts]
                buddy.seed_joints()
                self.assertEqual(posed, [(point.x, point.y) for point in buddy.pts])
                for tip, joint in db.LIMB_JOINT.items():
                    self.assertIsNone(buddy.joint_of(tip))
                    reach = math.hypot(buddy.body[joint].x - buddy.pts[tip].x,
                                       buddy.body[joint].y - buddy.pts[tip].y)
                    self.assertLess(reach, db.LEG_LEN)

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
        for state, crouch in ((db.WALK, 0), (db.IDLE, 0), (db.SIT, 0),
                              (db.SLEEP, 0), (db.CLIMB, 0),
                              (db.FREE, 0), (db.GRABBED, 0), (db.STUNNED, 0),
                              (db.ABDUCT, 0),
                              (db.WALK, 0.5), (db.WALK, 1), (db.IDLE, 1)):
            self.buddy.crouch = crouch
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
