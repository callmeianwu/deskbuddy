"""
DeskBuddy - a pixel art fella who lives on your taskbar.

He walks along the taskbar, scales the side edges of your real windows to get
up onto their title bars, sits and dangles his legs over the drop, and naps.
Windows that do not reach down near his feet are out of reach, and if one
closes or moves while he is on it he falls. Grab him with the left mouse and
throw him: he goes full ragdoll, tumbles across the screen, bounces off walls
and window edges, lies there stunned, then picks himself up and gets back to
work.

    SETUP (Windows)
        pip install PySide6
        python deskbuddy.py

    CONTROLS
        Left click + drag ... grab and throw him
        Double click ....... he jumps
        Right click ........ menu (sit, nap, toss, perching toggle, quit)
        Tray icon .......... backup quit if he ever gets stuck

    TWEAKING
        Every knob worth turning is in the CONFIG block below. SCALE changes
        his size; PALETTE changes his clothes.

    CANNOT SEE HIM?
        He lives on your primary monitor, standing on top of the taskbar. On
        start he prints the screen size and the exact line he walks along. If
        he is still nowhere, set DEBUG_OUTLINE = True below to draw a box
        around him and a line along his walking surface.

Notes: the overlay is a single click-through window covering the primary
monitor. Its input region is clipped to his body each frame, so clicks
anywhere else go straight to whatever is underneath. He tracks resolution
and taskbar changes at runtime. Fullscreen exclusive games will draw over
him - that is normal and expected.
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import random
import sys

VERSION = "build 9 - window reference fix"

_window = None   # the live Overlay. Must stay referenced or it is collected.

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "deskbuddy_log.txt")


def log(msg):
    print(msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# Keep Qt's logical pixels 1:1 with Win32's physical pixels, otherwise window
# rects from user32 land in the wrong place on scaled displays.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QFont,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

SCALE = 3               # size of one art pixel. 2 = small, 4 = chunky
FPS = 60
WALK_SPEED = 0.9 * SCALE
GRAVITY = 0.55 * SCALE
AIR_DRAG = 0.992
BOUNCE = 0.42           # energy kept when hitting a surface
FRICTION = 0.72         # horizontal slowdown on impact
MAX_THROW = 22 * SCALE  # speed cap so he cannot leave the solar system
CONSTRAINT_PASSES = 7
GRAB_RADIUS = 16 * SCALE
WINDOW_SCAN_MS = 400
MIN_LEDGE_WIDTH = 34 * SCALE
STUN_FRAMES = 34        # how long he lies there before getting up
SIT_SWING_SECONDS = 3.6
SIT_REST_SECONDS = 4.0
FLOOR_MARGIN = 2        # used only when no taskbar is found along the bottom
DEBUG_OUTLINE = "--debug" in sys.argv   # python deskbuddy.py --debug
BEACON_SECONDS = 5      # loud startup marker; set to 0 once you have seen him

PALETTE = {
    "O": QColor(28, 24, 38),      # outline
    "S": QColor(238, 195, 158),   # skin
    "H": QColor(92, 58, 46),      # hair
    "B": QColor(84, 148, 196),    # shirt
    "P": QColor(58, 62, 92),      # trousers
    "F": QColor(44, 40, 52),      # shoes
    "W": QColor(250, 250, 252),   # eye white
}

HEAD_ART = [
    "..HHHHH..",
    ".HHHHHHH.",
    "HHHHHHHHH",
    "HHSSSSSHH",
    "OSSSSSSSO",
    ".SSSSSSS.",
    ".SSSSSSS.",
    "..SSSSS..",
    "...OOO...",
]

TORSO_ART = [
    ".BBBBB.",
    "BBBBBBB",
    "BBBBBBB",
    "BBBBBBB",
    "BBBBBBB",
    ".BBBBB.",
    ".PPPPP.",
    ".PPPPP.",
]

# Body proportions, in art pixels before SCALE
NECK_LEN = 6 * SCALE
TORSO_LEN = 8 * SCALE
ARM_LEN = 9 * SCALE
LEG_LEN = 11 * SCALE

HEAD, CHEST, HIPS, HAND_L, HAND_R, FOOT_L, FOOT_R = range(7)

STICKS = [
    (HEAD, CHEST, NECK_LEN, 1.0),
    (CHEST, HIPS, TORSO_LEN, 1.0),
    (CHEST, HAND_L, ARM_LEN, 0.8),
    (CHEST, HAND_R, ARM_LEN, 0.8),
    (HIPS, FOOT_L, LEG_LEN, 0.9),
    (HIPS, FOOT_R, LEG_LEN, 0.9),
    (HEAD, HIPS, NECK_LEN + TORSO_LEN * 0.94, 0.55),   # keeps the spine honest
    (HAND_L, HIPS, ARM_LEN * 1.05, 0.18),
    (HAND_R, HIPS, ARM_LEN * 1.05, 0.18),
    (FOOT_L, FOOT_R, LEG_LEN * 0.8, 0.12),
]

RADII = {HEAD: 4.5 * SCALE, CHEST: 3 * SCALE, HIPS: 3 * SCALE}

# --------------------------------------------------------------------------
# WIN32: find the top edges of real windows so he has somewhere to perch
# --------------------------------------------------------------------------

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
SWP_NOZORDER, SWP_FRAMECHANGED = 0x0004, 0x0020

SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_Ghost",
    "TaskListThumbnailWnd", "ForegroundStaging", "XamlExplorerHostIslandWindow",
}


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _is_cloaked(hwnd):
    val = ctypes.c_int(0)
    hr = dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), ctypes.c_uint(DWMWA_CLOAKED),
        ctypes.byref(val), ctypes.sizeof(val))
    return hr == 0 and val.value != 0


def _frame_rect(hwnd):
    """Visible frame, ignoring the invisible resize border Win10/11 adds."""
    r = wt.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(r), ctypes.sizeof(r))
    if hr != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    return r


def _subtract(spans, cut_a, cut_b):
    """Remove the interval [cut_a, cut_b) from a list of (a, b) spans."""
    out = []
    for a, b in spans:
        if cut_b <= a or cut_a >= b:
            out.append((a, b))
            continue
        if cut_a > a:
            out.append((a, cut_a))
        if cut_b < b:
            out.append((cut_b, b))
    return out


def scan_window_ledges(self_hwnd, bounds, limit=14):
    """Top edges of visible windows, clipped where a higher window covers them.

    EnumWindows walks front to back, so anything already collected sits above
    whatever comes next and can occlude it.
    """
    collected = []
    walls = []
    covers = []  # (left, right, top, bottom) of windows above the current one

    def cb(hwnd, _lparam):
        if len(collected) >= limit:
            return False
        if hwnd == self_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _class_name(hwnd) in SKIP_CLASSES or _is_cloaked(hwnd):
            return True

        r = _frame_rect(hwnd)
        if r is None:
            return True
        w, h = r.right - r.left, r.bottom - r.top
        if w < MIN_LEDGE_WIDTH or h < 60:
            return True
        if r.top < bounds.top() + 4 or r.top > bounds.bottom() - 4:
            covers.append((r.left, r.right, r.top, r.bottom))
            return True

        spans = [(max(r.left + 2, bounds.left()),
                  min(r.right - 2, bounds.right()))]
        for cl, cr, ct, cb_ in covers:
            if ct <= r.top <= cb_:
                spans = _subtract(spans, cl, cr)
        for a, b in spans:
            if b - a >= MIN_LEDGE_WIDTH:
                collected.append({"x0": a, "x1": b, "y": r.top, "hwnd": hwnd})
                # the two side edges of this span are climbable
                for x, side in ((a, -1), (b, 1)):
                    walls.append({"x": x, "y0": r.top, "y1": r.bottom,
                                  "hwnd": hwnd, "side": side})

        covers.append((r.left, r.right, r.top, r.bottom))
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        pass
    return collected, walls


# --------------------------------------------------------------------------
# Verlet point
# --------------------------------------------------------------------------

class Pt:
    __slots__ = ("x", "y", "px", "py")

    def __init__(self, x=0.0, y=0.0):
        self.x = self.px = x
        self.y = self.py = y

    def place(self, x, y):
        """Move with no change in velocity."""
        dx, dy = x - self.x, y - self.y
        self.x, self.y = x, y
        self.px += dx
        self.py += dy

    def teleport(self, x, y):
        self.x = self.px = x
        self.y = self.py = y

    def kick(self, vx, vy):
        self.px = self.x - vx
        self.py = self.y - vy

    @property
    def vx(self):
        return self.x - self.px

    @property
    def vy(self):
        return self.y - self.py


# --------------------------------------------------------------------------
# The fella
# --------------------------------------------------------------------------

WALK, IDLE, SIT, SLEEP, GRABBED, FREE, STUNNED, CLIMB = range(8)
CLIMB_SPEED = 0.55 * SCALE
CLIMB_CHANCE = 0.55        # odds he takes a wall rather than walking past it
WALK_STRIDE = 5 * SCALE
CLIMB_STRIDE = 2.5 * SCALE


def step_cycle(phase, stride, lift):
    cycle = (phase / math.tau) % 1.0
    if cycle < 0.5:
        return stride * (1.0 - 4.0 * cycle), 0.0
    progress = (cycle - 0.5) * 2.0
    ease = progress * progress * (3.0 - 2.0 * progress)
    return stride * (2.0 * ease - 1.0), lift * math.sin(math.pi * progress) ** 2


class Buddy:
    def __init__(self, bounds, floor_y):
        self.bounds = bounds
        self.floor_y = floor_y     # top of the taskbar, not the screen bottom
        self.pts = [Pt() for _ in range(7)]
        self.state = WALK
        self.facing = 1
        self.phase = 0.0
        self.sit_time = 0.0
        self.timer = 0
        self.anchor_x = bounds.center().x()
        self.ledge = None          # dict from the scanner, or None for floor
        self.ledges = []
        self.walls = []
        self.wall = None           # wall he is currently climbing
        self.climb_y = 0.0
        self.stun = 0
        self.free_frames = 0
        self.blink = 0
        self.look = (0.0, 0.0)
        self.grab_idx = None
        self.spin = 0.0
        self.support = [None] * 7  # ledge a point landed on this frame
        self.allow_perch = True
        self.trail = []            # cursor samples, for throw velocity
        self.pose_state = self.state
        self.pose_from = None
        self.pose_frame = 0
        self.set_pose(self.anchor_x, floor_y)

    # -- pose ------------------------------------------------------------

    def ground_y(self):
        if self.state == CLIMB:
            return self.climb_y
        if self.ledge:
            return self.ledge["y"]
        return self.floor_y

    def set_pose(self, ax, gy):
        """Drop the ragdoll points onto a standing pose. Used to seed physics."""
        self.pose_state = self.state
        self.pose_from = None
        self.pose_frame = 0
        p = self.pts
        p[FOOT_L].teleport(ax - 2 * SCALE, gy)
        p[FOOT_R].teleport(ax + 2 * SCALE, gy)
        p[HIPS].teleport(ax, gy - LEG_LEN)
        p[CHEST].teleport(ax, gy - LEG_LEN - TORSO_LEN)
        p[HEAD].teleport(ax, gy - LEG_LEN - TORSO_LEN - NECK_LEN)
        p[HAND_L].teleport(ax - 3 * SCALE, gy - LEG_LEN - TORSO_LEN + ARM_LEN * 0.7)
        p[HAND_R].teleport(ax + 3 * SCALE, gy - LEG_LEN - TORSO_LEN + ARM_LEN * 0.7)

    def animate(self):
        """Kinematic poses. Physics is off; we just place the points."""
        p = self.pts
        ax, gy = self.anchor_x, self.ground_y()
        f = self.facing
        ph = self.phase

        if self.state != self.pose_state:
            if self.state == SIT:
                self.sit_time = 0.0
            settled = (WALK, IDLE, SIT, SLEEP)
            if self.state in settled and self.pose_state in settled:
                self.pose_from = [(point.x - ax, point.y - gy) for point in p]
                self.pose_frame = 0
            else:
                self.pose_from = None
            self.pose_state = self.state

        if self.state in (WALK, IDLE):
            swing = math.cos(ph) if self.state == WALK else 0.0
            bob = math.cos(ph) ** 2 * 0.65 * SCALE if self.state == WALK else \
                math.sin(ph * 0.35) * 0.5 * SCALE
            # idle turns to the viewer, so the profile lean drops out
            lean = 0.0 if self.state == IDLE else f
            hip_y = gy - LEG_LEN * 0.92 + bob
            chest_x = ax + lean * 0.6 * SCALE
            chest_y = hip_y - TORSO_LEN
            p[HIPS].place(ax, hip_y)
            p[CHEST].place(chest_x, chest_y)
            p[HEAD].place(ax + lean * 1.1 * SCALE, chest_y - NECK_LEN)
            for foot, offset in ((FOOT_L, 0.0), (FOOT_R, math.pi)):
                step, lift = step_cycle(ph + offset, WALK_STRIDE, 3.2 * SCALE)
                p[foot].place(ax + step * f, gy - lift)
            arm = 3.4 * SCALE
            hang = ARM_LEN * 0.72
            p[HAND_L].place(chest_x - swing * arm * f, chest_y + hang)
            p[HAND_R].place(chest_x + swing * arm * f, chest_y + hang)
            if self.state == IDLE:
                p[FOOT_L].place(ax - 2 * SCALE, gy)
                p[FOOT_R].place(ax + 2 * SCALE, gy)
                p[HAND_L].place(ax - 4 * SCALE, chest_y + hang)
                p[HAND_R].place(ax + 4 * SCALE, chest_y + hang)

        elif self.state == SIT:
            breathe = math.sin(ph * 0.35) * 0.3 * SCALE
            hip_y = gy - SCALE
            chest_y = hip_y - TORSO_LEN * 0.9 + breathe
            p[HIPS].place(ax, hip_y)
            p[CHEST].place(ax, chest_y)
            p[HEAD].place(ax, chest_y - NECK_LEN)
            cycle = self.sit_time % (SIT_REST_SECONDS + 2 * SIT_SWING_SECONDS)
            active_time = max(0.0, cycle - SIT_REST_SECONDS)
            swing = math.sin(math.tau * active_time / SIT_SWING_SECONDS)
            for foot, side in ((FOOT_L, -1), (FOOT_R, 1)):
                kick = max(0.0, swing * side) ** 2
                p[foot].place(ax + side * (3.2 + 0.35 * kick) * SCALE,
                              gy + (6.8 - 1.8 * kick) * SCALE)
            p[HAND_L].place(ax - 5 * SCALE, hip_y)
            p[HAND_R].place(ax + 5 * SCALE, hip_y)

        elif self.state == CLIMB:
            # facing points into the wall; body hugs it, hands reach overhead
            hip_y = self.climb_y - LEG_LEN * 0.7
            chest_y = hip_y - TORSO_LEN
            p[HIPS].place(ax, hip_y)
            p[CHEST].place(ax - f * 0.8 * SCALE, chest_y)
            p[HEAD].place(ax - f * 0.4 * SCALE, chest_y - NECK_LEN)
            for foot, hand, offset in ((FOOT_L, HAND_R, 0.0),
                                       (FOOT_R, HAND_L, math.pi)):
                step, release = step_cycle(ph + offset, CLIMB_STRIDE, SCALE)
                contact_x = ax + f * (3 * SCALE - release)
                p[foot].place(contact_x, self.climb_y - step)
                p[hand].place(contact_x, chest_y - ARM_LEN * 0.5 - step)

        elif self.state == SLEEP:
            breathe = math.sin(ph * 0.35) * 0.8 * SCALE
            hip_y = gy - 2 * SCALE
            chest_y = hip_y - TORSO_LEN * 0.88 + breathe * 0.3
            p[HIPS].place(ax, hip_y)
            p[CHEST].place(ax - f * 1.5 * SCALE, chest_y)
            p[HEAD].place(ax - f * 3.4 * SCALE, chest_y - NECK_LEN * 0.85)
            p[FOOT_L].place(ax + f * 5 * SCALE, gy - SCALE)
            p[FOOT_R].place(ax + f * 6.5 * SCALE, gy - SCALE)
            p[HAND_L].place(ax - f * 2 * SCALE, hip_y - SCALE)
            p[HAND_R].place(ax + f * 2 * SCALE, hip_y - SCALE)

        if self.pose_from is not None:
            self.pose_frame += 1
            progress = min(1.0, self.pose_frame / (FPS * 0.3))
            blend = progress * progress * (3.0 - 2.0 * progress)
            for point, (start_x, start_y) in zip(p, self.pose_from):
                point.place((ax + start_x) * (1.0 - blend) + point.x * blend,
                            (gy + start_y) * (1.0 - blend) + point.y * blend)
            if progress >= 1.0:
                self.pose_from = None

    # -- physics ----------------------------------------------------------

    def integrate(self):
        self.support = [None] * 7
        for i, pt in enumerate(self.pts):
            vx = (pt.x - pt.px) * AIR_DRAG
            vy = (pt.y - pt.py) * AIR_DRAG
            pt.px, pt.py = pt.x, pt.y
            pt.x += vx
            pt.y += vy + GRAVITY
        if self.spin:
            cx = sum(p.x for p in self.pts) / 7
            cy = sum(p.y for p in self.pts) / 7
            for pt in self.pts:
                dx, dy = pt.x - cx, pt.y - cy
                pt.x += -dy * self.spin
                pt.y += dx * self.spin
            self.spin *= 0.94
            if abs(self.spin) < 0.001:
                self.spin = 0.0

    def solve(self):
        for _ in range(CONSTRAINT_PASSES):
            for a, b, rest, stiff in STICKS:
                pa, pb = self.pts[a], self.pts[b]
                dx, dy = pb.x - pa.x, pb.y - pa.y
                d = math.hypot(dx, dy) or 0.0001
                diff = (d - rest) / d * 0.5 * stiff
                ox, oy = dx * diff, dy * diff
                if self.grab_idx != a:
                    pa.x += ox
                    pa.y += oy
                if self.grab_idx != b:
                    pb.x -= ox
                    pb.y -= oy
            self.collide()

    def collide(self):
        left, right = self.bounds.left(), self.bounds.right()
        floor = self.floor_y
        for i, pt in enumerate(self.pts):
            r = RADII.get(i, 2 * SCALE)

            if pt.x < left + r:
                pt.x = left + r
                pt.px = pt.x + pt.vx * BOUNCE
            elif pt.x > right - r:
                pt.x = right - r
                pt.px = pt.x + pt.vx * BOUNCE

            # Ledges get first claim, before the hard screen floor. Otherwise a
            # point falling fast enough to clear the taskbar in one step lands
            # on the bottom of the screen instead of on top of the taskbar.

            # A point that already landed this frame keeps resting there, even
            # though the constraint solver has since flipped its velocity
            # upward. Without this it falls through on the next pass.
            held = self.support[i]
            if held is not None:
                top, x0, x1 = held
                if x0 < pt.x < x1:
                    if pt.y > top:
                        pt.y = top
                    continue
                self.support[i] = None

            landed = False
            for lg in self.ledges:
                top = lg["y"] - r
                if not (lg["x0"] < pt.x < lg["x1"]):
                    continue
                # catch him crossing downward, or sinking after the solver
                # pulled him under
                if pt.py <= top + 1 and pt.y > top:
                    self._land(pt, top)
                    self.support[i] = (top, lg["x0"], lg["x1"])
                    landed = True
                    break
            if landed:
                continue

            if pt.y > floor - r:
                self._land(pt, floor - r)
                continue

            if pt.y < self.bounds.top() + r:
                pt.y = self.bounds.top() + r
                pt.py = pt.y - abs(pt.vy) * BOUNCE

    def _land(self, pt, y):
        vx, vy = pt.vx, pt.vy
        pt.y = y
        pt.py = pt.y + vy * BOUNCE
        pt.px = pt.x - vx * FRICTION

    def energy(self):
        return sum(abs(p.vx) + abs(p.vy) for p in self.pts)

    # -- state changes ----------------------------------------------------

    def go_free(self, vx=0.0, vy=0.0, spin=0.0):
        self.state = FREE
        self.pose_state = FREE
        self.pose_from = None
        self.grab_idx = None
        self.spin = spin
        self.free_frames = 0
        self.stun = 0
        for pt in self.pts:
            pt.kick(vx + random.uniform(-0.6, 0.6),
                    vy + random.uniform(-0.6, 0.6))

    def stand_up(self):
        """Find whatever he is lying on and start walking again."""
        cx = sum(p.x for p in self.pts) / 7
        low = max(p.y for p in self.pts)
        # The screen bottom is the fallback; anything closer under him wins.
        best, best_gap = None, abs(self.floor_y - low)
        for lg in self.ledges:
            if lg["hwnd"] != -1 and not self.allow_perch:
                continue
            if lg["x0"] < cx < lg["x1"]:
                # bias: a real surface beats the bare screen bottom on a tie
                gap = abs(lg["y"] - low) - 10 * SCALE
                if gap < best_gap:
                    best, best_gap = lg, gap
        if best_gap > 30 * SCALE:
            best = None
        self.ledge = best
        self.anchor_x = min(max(cx, self.walk_min()), self.walk_max())
        self.state = WALK
        self.phase = random.uniform(0, 6.28)
        self.facing = 1 if random.random() < 0.5 else -1
        self.set_pose(self.anchor_x, self.ground_y())

    def walk_min(self):
        return (self.ledge["x0"] if self.ledge else self.bounds.left()) + 6 * SCALE

    def walk_max(self):
        return (self.ledge["x1"] if self.ledge else self.bounds.right()) - 6 * SCALE

    def wall_ahead(self):
        """A window edge he has just walked into, that is worth climbing."""
        gy = self.ground_y()
        for w in self.walls:
            if self.ledge and w["hwnd"] == self.ledge["hwnd"]:
                continue
            distance = (w["x"] - self.anchor_x) * self.facing
            if not -WALK_SPEED <= distance <= 6 * SCALE + WALK_SPEED * 1.6:
                continue
            if w["side"] * self.facing >= 0:
                continue
            # the edge has to reach down to about his feet, and lead somewhere
            if w["y1"] < gy - 12 * SCALE or w["y0"] > gy - 16 * SCALE:
                continue
            return w
        return None

    def climb_candidates(self):
        """Ledges he could hop up to from where he stands."""
        gy = self.ground_y()
        out = []
        for lg in self.ledges:
            if lg is self.ledge:
                continue
            if lg["hwnd"] != -1 and not self.allow_perch:
                continue
            rise = gy - lg["y"]
            if 8 * SCALE < rise < 34 * SCALE and \
                    lg["x0"] - 20 * SCALE < self.anchor_x < lg["x1"] + 20 * SCALE:
                out.append(lg)
        return out

    # -- per-frame --------------------------------------------------------

    def update(self, cursor):
        if self.state == WALK:
            self.phase += WALK_SPEED * math.pi / (2 * WALK_STRIDE)
        elif self.state == CLIMB:
            self.phase += CLIMB_SPEED * math.pi / (2 * CLIMB_STRIDE)
        else:
            self.phase += 0.06
        if self.state == SIT:
            self.sit_time += 1.0 / FPS
        self.timer -= 1
        if self.blink > 0:
            self.blink -= 1
        elif random.random() < 0.006:
            self.blink = 8

        hx, hy = self.pts[HEAD].x, self.pts[HEAD].y
        dx, dy = cursor.x() - hx, cursor.y() - hy
        d = math.hypot(dx, dy) or 1
        near = min(1.0, 260.0 / d)
        self.look = (max(-1, min(1, dx / 90)) * near,
                     max(-1, min(1, dy / 90)) * near)

        if self.state == GRABBED:
            pt = self.pts[self.grab_idx]
            pt.place(cursor.x(), cursor.y())
            self.integrate()
            pt.teleport(cursor.x(), cursor.y())
            self.solve()
            return

        if self.state in (FREE, STUNNED):
            self.integrate()
            self.solve()
            if self.state == FREE:
                self.free_frames += 1
                if self.energy() < 1.4 * SCALE:
                    self.stun += 1
                    if self.stun > 6:
                        self.state = STUNNED
                        self.timer = STUN_FRAMES
                else:
                    self.stun = 0
                # Never let him ragdoll forever: if he is still twitching
                # after a few seconds, call it and stand him up anyway.
                if self.free_frames > FPS * 6:
                    self.state = STUNNED
                    self.timer = 12
            elif self.timer <= 0:
                self.stand_up()
            return

        if self.state == CLIMB:
            w = next((x for x in self.walls
                      if x["hwnd"] == self.wall["hwnd"]
                      and abs(x["x"] - self.wall["x"]) < 24 * SCALE
                      and x["side"] == self.wall["side"]), None)
            if w is None:                      # window moved or closed mid-climb
                self.wall = None
                self.go_free(self.facing * -2.0, 1.0, random.uniform(-.05, .05))
                return
            self.wall = w
            self.anchor_x = w["x"] + w["side"] * 3 * SCALE
            self.climb_y -= CLIMB_SPEED
            if self.climb_y <= w["y0"] + 2 * SCALE:
                top = next((l for l in self.ledges
                            if l["hwnd"] == w["hwnd"]
                            and l["x0"] - 4 <= w["x"] <= l["x1"] + 4), None)
                self.wall = None
                if top is None:
                    self.go_free(0, 0)
                    return
                self.ledge = top
                self.anchor_x = min(max(w["x"], top["x0"] + 6 * SCALE),
                                    top["x1"] - 6 * SCALE)
                self.state = WALK
                self.timer = random.randint(120, 260)
                self.set_pose(self.anchor_x, self.ground_y())
            else:
                self.animate()
            return

        # grounded behaviours
        if self.ledge is not None:
            live = next((l for l in self.ledges if l["hwnd"] == self.ledge["hwnd"]
                         and l["x0"] < self.anchor_x < l["x1"]), None)
            if live is None:
                self.ledge = None
                self.go_free(0, 0)          # his window moved out from under him
                return
            self.ledge = live

        if self.state == WALK:
            self.anchor_x += WALK_SPEED * self.facing
            if self.allow_perch:
                w = self.wall_ahead()
                if w is not None and random.random() < CLIMB_CHANCE:
                    self.wall = w
                    self.facing = -w["side"]
                    self.climb_y = self.ground_y()
                    self.ledge = None
                    self.anchor_x = w["x"] + w["side"] * 3 * SCALE
                    self.state = CLIMB
                    self.animate()
                    return
            lo, hi = self.walk_min(), self.walk_max()
            if self.anchor_x < lo or self.anchor_x > hi:
                self.anchor_x = min(max(self.anchor_x, lo), hi)
                if self.ledge and random.random() < 0.35:
                    self.ledge = None       # step off the edge on purpose
                    self.go_free(self.facing * 1.5, -1.0)
                    return
                self.facing *= -1
            if self.timer <= 0:
                self.pick_idle()
        elif self.state in (IDLE, SIT, SLEEP):
            if self.timer <= 0:
                self.state = WALK
                self.timer = random.randint(120, 320)
                self.facing = 1 if random.random() < 0.5 else -1

        self.animate()

    def pick_idle(self):
        roll = random.random()
        if roll < 0.15 and self.allow_perch and self.climb_candidates():
            target = random.choice(self.climb_candidates())
            self.ledge = target
            self.anchor_x = min(max(self.anchor_x, target["x0"] + 8 * SCALE),
                                target["x1"] - 8 * SCALE)
            self.set_pose(self.anchor_x, self.ground_y())
            self.state = WALK
            self.timer = random.randint(140, 300)
        elif roll < 0.72:
            self.state = SIT
            self.timer = random.randint(12 * FPS, 22 * FPS)
        elif roll < 0.82:
            self.state = SLEEP
            self.timer = random.randint(300, 700)
        elif roll < 0.92:
            self.state = IDLE
            self.timer = random.randint(90, 220)
        else:
            self.timer = random.randint(160, 380)

    def bounds_rect(self, pad):
        xs = [p.x for p in self.pts]
        ys = [p.y for p in self.pts]
        return QRect(int(min(xs) - pad), int(min(ys) - pad),
                     int(max(xs) - min(xs) + pad * 2),
                     int(max(ys) - min(ys) + pad * 2))


# --------------------------------------------------------------------------
# Overlay window
# --------------------------------------------------------------------------

def build_pixmap(art):
    w, h = len(art[0]), len(art)
    pm = QPixmap(w * SCALE, h * SCALE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            if ch != ".":
                p.fillRect(x * SCALE, y * SCALE, SCALE, SCALE, PALETTE[ch])
    p.end()
    return pm


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None)
        # These are exactly the flags overlay_test.py uses, which is known to
        # render on this machine. Do not add to them casually.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # Primary monitor only. Spanning every screen meant negative window
        # coordinates on multi-monitor setups and a buddy who could wander off
        # onto a display you were not looking at.
        scr = QGuiApplication.primaryScreen()
        self.screen_ref = scr
        self.virt = scr.geometry()
        work = scr.availableGeometry()
        # availableGeometry stops at the taskbar, so its bottom is the surface
        # he stands on. If the taskbar is hidden or on a side edge, fall back
        # to just above the screen edge so he is never drawn off-screen.
        if work.bottom() < self.virt.bottom() - 4:
            self.floor_y = work.bottom() + 1
        else:
            self.floor_y = self.virt.bottom() - FLOOR_MARGIN
        self.setGeometry(self.virt)

        self.head_pm = build_pixmap(HEAD_ART)
        self.torso_pm = build_pixmap(TORSO_ART)

        self.buddy = Buddy(self.virt, self.floor_y)
        self.buddy.ledges = self.floor_ledges()
        self.buddy.ledge = self.buddy.ledges[0]
        self.buddy.set_pose(self.buddy.anchor_x, self.floor_y)
        self.clickthrough = None
        self.dragging = False
        self.frames = 0
        self.paints = 0
        self.paint_error = None

        log(f"DeskBuddy on {self.screen_ref.name()}  "
            f"screen {self.virt.width()}x{self.virt.height()} "
            f"at ({self.virt.x()},{self.virt.y()})  "
            f"walking line y={self.floor_y}  "
            f"spawn x={int(self.buddy.anchor_x)}  "
            f"dpr={self.screen_ref.devicePixelRatio()}  "
            f"scale={SCALE}")

        log("step: about to show()")
        self.show()
        log("step: show() returned")
        self.harden()
        log("step: harden() returned")
        try:
            self.make_tray()
            log("step: tray created")
        except Exception:
            import traceback
            log("tray failed (continuing without it):\n"
                + traceback.format_exc())

        self.clock = QTimer(self)
        self.clock.timeout.connect(self.tick)
        self.clock.start(int(1000 / FPS))

        self.scanner = QTimer(self)
        self.scanner.timeout.connect(self.rescan)
        self.scanner.start(WINDOW_SCAN_MS)

        self.topper = QTimer(self)
        self.topper.timeout.connect(self.raise_topmost)
        self.topper.start(900)
        log("step: timers started, waiting for first tick")

        QTimer.singleShot(2500, self.watchdog)

        scr.geometryChanged.connect(self.screen_changed)
        scr.availableGeometryChanged.connect(self.screen_changed)

    def watchdog(self):
        """If the clock never ticked, the event loop is not running our timer."""
        if self.frames == 0:
            log("WATCHDOG: 2.5s elapsed and tick() has never run. The clock "
                "timer is not firing - the event loop is blocked or dead.")
        elif self.paints == 0:
            log(f"WATCHDOG: {self.frames} ticks but 0 paints. The window is "
                "not being asked to repaint.")
        else:
            log(f"WATCHDOG: healthy - {self.frames} ticks, {self.paints} "
                f"paints, visible={self.isVisible()}")

    def screen_changed(self, *_):
        """Resolution switch, docking, or the taskbar moving or resizing."""
        scr = self.screen_ref
        self.virt = scr.geometry()
        work = scr.availableGeometry()
        if work.bottom() < self.virt.bottom() - 4:
            self.floor_y = work.bottom() + 1
        else:
            self.floor_y = self.virt.bottom() - FLOOR_MARGIN
        self.setGeometry(self.virt)
        b = self.buddy
        b.bounds = self.virt
        b.floor_y = self.floor_y
        b.ledges = self.floor_ledges()
        b.ledge = b.ledges[0]
        b.wall = None
        b.anchor_x = min(max(b.anchor_x, self.virt.left() + 40),
                         self.virt.right() - 40)
        if b.state == CLIMB:
            b.state = WALK
        b.set_pose(b.anchor_x, self.floor_y)
        log(f"DeskBuddy: display changed -> {self.virt.width()}"
            f"x{self.virt.height()}, walking line y={self.floor_y}")

    # -- windows plumbing -------------------------------------------------

    def hwnd(self):
        return int(self.winId())

    def harden(self):
        """Raise above the taskbar. Deliberately does NOT touch extended
        window styles - rewriting them on a layered window blanked it."""
        self.raise_()
        self.raise_topmost()

    def raise_topmost(self):
        user32.SetWindowPos(self.hwnd(), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def floor_ledges(self):
        """The single surface he always has: the top of the taskbar."""
        return [{"x0": self.virt.left() + 4, "x1": self.virt.right() - 4,
                 "y": self.floor_y, "hwnd": -1}]

    def rescan(self):
        led, walls = self.floor_ledges(), []
        if self.buddy.allow_perch:
            found, walls = scan_window_ledges(self.hwnd(), self.virt)
            led += found
        self.buddy.ledges = led
        self.buddy.walls = walls

    def make_tray(self):
        self.tray = QSystemTrayIcon(QIcon(self.head_pm), self)
        self.tray.setToolTip("DeskBuddy")
        m = QMenu()
        act = QAction("Quit DeskBuddy", m)
        act.triggered.connect(QApplication.quit)
        m.addAction(act)
        self.tray.setContextMenu(m)
        self._tray_menu = m
        self.tray.show()

    # -- loop -------------------------------------------------------------

    def tick(self):
        try:
            self._tick()
        except Exception:
            if self.paint_error is None:
                import traceback
                self.paint_error = traceback.format_exc()
                log("tick() raised (animation stopped):\n" + self.paint_error)

    def _tick(self):
        cursor = QCursor.pos()
        if self.dragging:
            self.buddy.trail.append((cursor.x(), cursor.y()))
            if len(self.buddy.trail) > 6:
                self.buddy.trail.pop(0)
        self.buddy.update(cursor)

        new = self.buddy.bounds_rect(10 * SCALE)
        self.frames += 1
        beacon = self.frames < FPS * BEACON_SECONDS

        # During the beacon the window is left exactly as constructed - the
        # same configuration overlay_test.py proved works here. Only after it
        # do we touch mouse transparency, so if he vanishes at that moment we
        # know precisely what did it.
        if beacon:
            if self.frames == 1:
                log(f"beacon on for {BEACON_SECONDS}s - look for a magenta "
                    f"panel in the middle of the screen")
        else:
            if not self.dragging:
                near = new.adjusted(-4 * SCALE, -4 * SCALE,
                                    4 * SCALE, 4 * SCALE).contains(cursor)
                want = not near
                if want != self.clickthrough:
                    if self.clickthrough is None:
                        log("beacon over, enabling click-through now")
                    self.clickthrough = want
                    self.setAttribute(Qt.WA_TransparentForMouseEvents, want)
                    # Qt changes WS_EX_TRANSPARENT on the live window. On a
                    # layered window that can leave the surface stale until
                    # the frame change is acknowledged, so say so explicitly.
                    user32.SetWindowPos(
                        self.hwnd(), wt.HWND(0), 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                        | SWP_NOZORDER | SWP_FRAMECHANGED)
                    self.repaint()

        if self.frames == FPS:
            log(f"1s in: {self.paints} paints, buddy at "
                f"{int(self.buddy.pts[HEAD].x)},{int(self.buddy.pts[HEAD].y)}, "
                f"state {self.buddy.state}")
            if self.paints == 0:
                log("  paintEvent never fired - the window is not being asked "
                    "to draw at all")

        self.update()

    # -- input ------------------------------------------------------------

    def _nearest(self, gp):
        best, bd = None, GRAB_RADIUS
        for i, pt in enumerate(self.buddy.pts):
            d = math.hypot(pt.x - gp.x(), pt.y - gp.y())
            if d < bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        gp = ev.globalPosition().toPoint()
        idx = self._nearest(gp)
        if idx is None:
            idx = CHEST
        b = self.buddy
        if b.state not in (FREE, STUNNED, GRABBED):
            b.set_pose(b.anchor_x, b.ground_y())
        b.state = GRABBED
        b.grab_idx = idx
        b.spin = 0.0
        b.trail = [(gp.x(), gp.y())]
        self.dragging = True
        self.grabMouse()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton or not self.dragging:
            return
        self.dragging = False
        self.releaseMouse()
        t = self.buddy.trail
        vx = vy = 0.0
        if len(t) >= 2:
            steps = len(t) - 1
            vx = (t[-1][0] - t[0][0]) / steps
            vy = (t[-1][1] - t[0][1]) / steps
        speed = math.hypot(vx, vy)
        if speed > MAX_THROW:
            vx, vy = vx / speed * MAX_THROW, vy / speed * MAX_THROW
        self.buddy.go_free(vx, vy, spin=max(-0.09, min(0.09, vx * 0.006)))
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.buddy.go_free(self.buddy.facing * 3.0, -11.0 * SCALE * 0.5)
            ev.accept()

    def contextMenuEvent(self, ev):
        b = self.buddy
        m = QMenu()
        m.addAction("Take a seat", lambda: self.force(SIT, 18 * FPS))
        m.addAction("Have a nap", lambda: self.force(SLEEP, 700))
        m.addAction("Back to walking", lambda: self.force(WALK, 300))
        m.addSeparator()
        m.addAction("Toss him", lambda: b.go_free(
            random.uniform(-14, 14), random.uniform(-16, -8),
            random.uniform(-0.08, 0.08)))
        m.addAction("Send him home", self.reset)
        perch = QAction("Perch on windows", m, checkable=True)
        perch.setChecked(b.allow_perch)
        perch.toggled.connect(self.set_perch)
        m.addSeparator()
        m.addAction(perch)
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(ev.globalPos())
        ev.accept()

    def force(self, state, dur):
        b = self.buddy
        if b.state in (FREE, GRABBED, STUNNED, CLIMB):
            return  # mid-air or mid-climb; let him finish what he is doing
        b.state = state
        b.timer = dur

    def set_perch(self, on):
        self.buddy.allow_perch = on
        if not on and self.buddy.ledge and self.buddy.ledge["hwnd"] != -1:
            self.buddy.ledge = None
            self.buddy.go_free(0, 0)
        self.rescan()

    def reset(self):
        b = self.buddy
        b.ledges = self.floor_ledges() + [l for l in b.ledges if l["hwnd"] != -1]
        b.ledge = b.ledges[0]
        b.wall = None
        b.state = WALK
        b.timer = 200
        b.spin = 0.0
        b.anchor_x = self.virt.center().x()
        b.set_pose(b.anchor_x, self.floor_y)

    # -- drawing ----------------------------------------------------------

    def paintEvent(self, _ev):
        self.paints += 1
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.translate(-self.virt.x(), -self.virt.y())

        if self.frames < FPS * BEACON_SECONDS:
            self.draw_beacon(p)

        # The sprite is drawn inside a guard. If it throws, the beacon above
        # still renders, which tells us the window is fine and the drawing
        # code is not. Without this the exception goes to stderr and you see
        # nothing at all.
        try:
            self.draw_buddy(p)
        except Exception:
            if self.paint_error is None:
                import traceback
                self.paint_error = traceback.format_exc()
                log("drawing the buddy raised:\n" + self.paint_error)
        p.end()

    def draw_beacon(self, p):
        """Loud, unmissable startup marker drawn in known-good screen space."""
        v = self.virt
        cx, cy = v.center().x(), v.center().y()
        left = self.frames // FPS
        p.fillRect(cx - 250, cy - 110, 500, 220, QColor(255, 0, 170, 235))
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 22, QFont.Bold))
        p.drawText(cx - 225, cy - 60, "DESKBUDDY IS RUNNING")
        p.setFont(QFont("Segoe UI", 11))
        p.drawText(cx - 225, cy - 28,
                   f"The little guy is drawn below, at actual size.")
        p.drawText(cx - 225, cy - 8,
                   f"He normally lives on the taskbar at y={self.floor_y}.")
        remaining = max(0, BEACON_SECONDS - left)
        p.drawText(cx - 225, cy + 96,
                   f"This panel disappears in {remaining}s.")

        # draw him a second time here, in the middle of the screen, so we can
        # tell a broken sprite apart from a buddy hidden behind the taskbar
        b = self.buddy
        p.save()
        p.translate(cx - b.pts[HIPS].x, (cy + 40) - b.pts[HIPS].y)
        try:
            self.draw_buddy(p)
        except Exception:
            p.setPen(QColor(255, 255, 0))
            p.setFont(QFont("Segoe UI", 12, QFont.Bold))
            p.drawText(int(b.pts[HIPS].x) - 100, int(b.pts[HIPS].y),
                       "SPRITE FAILED TO DRAW")
        p.restore()

        p.fillRect(v.left(), int(self.floor_y) - 2, v.width(), 4,
                   QColor(0, 255, 180, 230))
        p.setPen(QColor(0, 255, 180))
        p.setFont(QFont("Segoe UI", 12, QFont.Bold))
        p.drawText(v.left() + 20, int(self.floor_y) - 10,
                   "the real buddy walks along this line")

    def draw_buddy(self, p):
        b = self.buddy
        pts = b.pts
        chest, hips, head = pts[CHEST], pts[HIPS], pts[HEAD]

        # contact shadow, only when he is settled on something
        if b.state in (WALK, IDLE, SIT, SLEEP):
            gy = b.ground_y()
            w = 13 * SCALE
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 46))
            p.drawEllipse(QPoint(int(b.anchor_x), int(gy + SCALE)),
                          int(w / 2), int(1.6 * SCALE))
            p.setBrush(Qt.NoBrush)

        if b.state == SIT:
            for hand, foot, side in ((HAND_L, FOOT_L, -1), (HAND_R, FOOT_R, 1)):
                shoulder = Pt(chest.x + side * 2.5 * SCALE, chest.y)
                self.arm(p, shoulder, pts[hand], -side)
                self.seated_leg(p, hips, pts[foot], side)
            self.part(p, self.torso_pm, chest, hips)
        elif b.state == IDLE:
            # front view: both legs and arms read as near the camera
            for foot in (FOOT_L, FOOT_R):
                self.limb(p, hips, pts[foot], PALETTE["P"], PALETTE["F"], True)
            self.part(p, self.torso_pm, chest, hips)
            for hand, side in ((HAND_L, -1), (HAND_R, 1)):
                shoulder = Pt(chest.x + side * 2.5 * SCALE, chest.y)
                self.arm(p, shoulder, pts[hand], -side)
        else:
            back, front = (HAND_L, FOOT_L), (HAND_R, FOOT_R)
            if b.facing < 0:
                back, front = front, back

            self.limb(p, hips, pts[back[1]], PALETTE["P"], PALETTE["F"], True)
            self.arm(p, chest, pts[back[0]], b.facing)
            self.part(p, self.torso_pm, chest, hips)
            self.limb(p, hips, pts[front[1]], PALETTE["P"], PALETTE["F"], True)
            self.arm(p, chest, pts[front[0]], b.facing)
        self.draw_head(p, head, chest)

        if b.state == SLEEP:
            self.zzz(p, head)
        if DEBUG_OUTLINE:
            r = b.bounds_rect(6 * SCALE)
            p.setBrush(QColor(255, 0, 170, 50))
            p.setPen(QPen(QColor(255, 0, 170), 2))
            p.drawRect(r)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 255, 180), 3))
            p.drawLine(self.virt.left(), int(b.floor_y),
                       self.virt.right(), int(b.floor_y))
            p.setPen(QPen(QColor(255, 220, 0), 2))
            for lg in b.ledges:
                if lg["hwnd"] != -1:
                    p.drawLine(int(lg["x0"]), int(lg["y"]),
                               int(lg["x1"]), int(lg["y"]))
            names = {WALK: "walk", IDLE: "idle", SIT: "sit", SLEEP: "sleep",
                     GRABBED: "grabbed", FREE: "ragdoll", STUNNED: "stunned",
                     CLIMB: "climb"}
            p.setPen(QColor(255, 255, 255))
            p.fillRect(self.virt.left() + 10, self.virt.top() + 10, 430, 96,
                       QColor(0, 0, 0, 190))
            p.drawText(self.virt.left() + 22, self.virt.top() + 34,
                       f"state {names.get(b.state)}   head "
                       f"{head.x:.0f},{head.y:.0f}   feet "
                       f"{max(b.pts[FOOT_L].y, b.pts[FOOT_R].y):.0f}")
            p.drawText(self.virt.left() + 22, self.virt.top() + 58,
                       f"floor y={b.floor_y}   screen {self.virt.width()}x"
                       f"{self.virt.height()}   ledges {len(b.ledges)}   "
                       f"walls {len(b.walls)}")
            p.drawText(self.virt.left() + 22, self.virt.top() + 82,
                       f"box {r.x()},{r.y()} {r.width()}x{r.height()}   "
                       f"clickthrough {self.clickthrough}")

    def arm(self, p, a, b_, bend):
        """Two segments with an implied elbow, so it reads as an arm."""
        mx, my = (a.x + b_.x) / 2, (a.y + b_.y) / 2
        dx, dy = b_.x - a.x, b_.y - a.y
        d = math.hypot(dx, dy) or 1
        nx, ny = -dy / d, dx / d
        slack = max(0.0, ARM_LEN - d) * 0.5 + 1.2 * SCALE
        ex, ey = mx + nx * slack * bend, my + ny * slack * bend
        pen = QPen(PALETTE["S"], int(2 * SCALE), Qt.SolidLine, Qt.SquareCap)
        p.setPen(pen)
        p.drawLine(int(a.x), int(a.y), int(ex), int(ey))
        p.drawLine(int(ex), int(ey), int(b_.x), int(b_.y))
        p.setPen(QPen(PALETTE["B"], int(2.4 * SCALE), Qt.SolidLine, Qt.SquareCap))
        p.drawLine(int(a.x), int(a.y),
                   int(a.x + (ex - a.x) * 0.45), int(a.y + (ey - a.y) * 0.45))
        p.fillRect(int(b_.x - SCALE), int(b_.y - SCALE),
                   int(2 * SCALE), int(2 * SCALE), PALETTE["S"])

    def limb(self, p, a, b_, col, shoe, with_shoe):
        dx, dy = b_.x - a.x, b_.y - a.y
        d = math.hypot(dx, dy) or 1
        nx, ny = dy / d * self.buddy.facing, -dx / d * self.buddy.facing
        slack = math.sqrt(max(0.0, LEG_LEN ** 2 - d ** 2)) * 0.5
        ex, ey = (a.x + b_.x) / 2 + nx * slack, (a.y + b_.y) / 2 + ny * slack
        p.setPen(QPen(col, int(2.4 * SCALE), Qt.SolidLine, Qt.SquareCap))
        p.drawLine(int(a.x), int(a.y), int(ex), int(ey))
        p.drawLine(int(ex), int(ey), int(b_.x), int(b_.y))
        if with_shoe:
            ang = math.degrees(math.atan2(b_.y - ey, b_.x - ex)) - 90
            if self.buddy.state in (WALK, IDLE):
                ang = 0
            p.save()
            p.translate(b_.x, b_.y)
            p.rotate(ang)
            p.fillRect(int(-2 * SCALE), int(-SCALE),
                       int(4 * SCALE), int(2 * SCALE), shoe)
            p.restore()

    def seated_leg(self, p, hips, foot, side):
        kick = max(0.0, min(1.0, (7.8 - (foot.y - hips.y) / SCALE) / 1.8))
        thigh_x = hips.x + side * SCALE
        knee_x = hips.x + side * 3.2 * SCALE
        knee_y = hips.y + 1.8 * SCALE
        p.setPen(QPen(PALETTE["P"], int(2.4 * SCALE), Qt.SolidLine, Qt.SquareCap))
        p.drawLine(int(thigh_x), int(hips.y), int(knee_x), int(knee_y))
        p.drawLine(int(knee_x), int(knee_y), int(foot.x), int(foot.y))
        shoe_height = (2.0 + kick * 0.5) * SCALE
        p.fillRect(int(foot.x - 2 * SCALE), int(foot.y - SCALE),
                   4 * SCALE, int(shoe_height), PALETTE["F"])
        if kick > 0.45:
            p.fillRect(int(foot.x - 1.5 * SCALE),
                       int(foot.y - SCALE + shoe_height - SCALE),
                       3 * SCALE, max(1, SCALE // 2), PALETTE["O"])

    def part(self, p, pm, top, bottom):
        ang = math.degrees(math.atan2(bottom.y - top.y, bottom.x - top.x)) - 90
        cx, cy = (top.x + bottom.x) / 2, (top.y + bottom.y) / 2
        p.save()
        p.translate(cx, cy)
        p.rotate(ang)
        p.drawPixmap(int(-pm.width() / 2), int(-pm.height() / 2), pm)
        p.restore()

    def draw_head(self, p, head, chest):
        b = self.buddy
        ang = math.degrees(math.atan2(head.y - chest.y, head.x - chest.x)) + 90
        pm = self.head_pm
        p.save()
        p.translate(head.x, head.y)
        p.rotate(ang)
        p.drawPixmap(int(-pm.width() / 2), int(-pm.height() / 2), pm)

        lx, ly = b.look
        ex = int(lx * SCALE)
        ey = int(ly * SCALE * 0.7)
        eye_y = int(-0.5 * SCALE) + ey
        for sx in (-2, 2):
            bx = int(sx * SCALE) + ex - SCALE // 2
            if b.state == SLEEP or b.blink > 4:
                p.fillRect(bx - SCALE // 2, eye_y, 2 * SCALE,
                           max(1, SCALE // 2), PALETTE["O"])
            else:
                p.fillRect(bx - SCALE // 2, eye_y - SCALE // 2,
                           2 * SCALE, int(1.6 * SCALE), PALETTE["W"])
                p.fillRect(bx, eye_y - SCALE // 2, SCALE, int(1.4 * SCALE),
                           PALETTE["O"])
        p.restore()

    def zzz(self, p, head):
        t = self.buddy.phase
        p.setPen(QPen(PALETTE["O"], max(1, SCALE // 2)))
        for i in range(3):
            ph = (t * 0.5 + i * 2.1) % 6.0
            if ph > 4.5:
                continue
            s = SCALE * (1.0 + ph * 0.22)
            x = head.x + 6 * SCALE + ph * 2.2 * SCALE
            y = head.y - 5 * SCALE - ph * 3.4 * SCALE
            p.drawLine(int(x), int(y), int(x + 2 * s), int(y))
            p.drawLine(int(x + 2 * s), int(y), int(x), int(y + 2 * s))
            p.drawLine(int(x), int(y + 2 * s), int(x + 2 * s), int(y + 2 * s))


def main():
    if sys.platform != "win32":
        print("DeskBuddy uses Win32 window enumeration. Windows only.")
        return 1
    try:
        open(LOG_PATH, "w").close()
    except Exception:
        pass
    log(f"DeskBuddy {VERSION}  python {sys.version.split()[0]}  "
        f"file {os.path.abspath(__file__)}")
    app = QApplication(sys.argv)
    log("step: QApplication created")
    app.setQuitOnLastWindowClosed(False)
    try:
        # Hold a reference. Without one, Python garbage-collects the widget
        # moments after construction, taking the C++ window, the tray icon
        # and every timer with it - the app then sits in an empty event loop
        # forever, alive in Task Manager with nothing on screen.
        global _window
        _window = Overlay()
        app._deskbuddy = _window
        _window.destroyed.connect(
            lambda: log("WARNING: the overlay window was destroyed"))
        log("step: Overlay() constructed and retained, entering event loop")
    except Exception:
        import traceback
        log("DeskBuddy failed to start:\n" + traceback.format_exc())
        raise
    rc = app.exec()
    log(f"event loop exited with code {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
