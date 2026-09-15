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
        Chase him ........... creep the mouse cursor up on him and he only
                              eyes it and backs away; lunge at him and he
                              bolts, arms flailing, and can run face first
                              into a window edge and ragdoll off it
        Double click ....... he jumps
        Right click ........ menu (sit, nap, toss, perching toggle, quit)
        Tray icon .......... turn the cursor fright on or off, and a backup
                             quit if he ever gets stuck

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
    QPolygon,
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
# Nerve. It is fed by the cursor closing in on him, not by the cursor merely
# being close, so a slow careful approach never sets him off - a lunge does.
SPOOK_ENABLED = True        # start with the whole panic behaviour switched on
SPOOK_RADIUS = 42 * SCALE   # outside this the cursor is just scenery
SPOOK_GAIN = 0.019          # nerve per pixel of ground the cursor takes off him
SPOOK_DECAY = 0.013         # bled off every frame, so a creeping cursor nets zero
SPOOK_LOOM = 0.028          # extra for a cursor fidgeting right on top of him
SPOOK_CORNERED = 0.014      # and for having nowhere left to back away to
WARY_LEVEL = 0.34           # he stops, faces you and backs away
WARY_SPEED = 0.42 * SCALE
WARY_STRIDE = 2.6 * SCALE
TEETER_FRAMES = int(FPS * 0.45)      # heels over the drop before he goes over
DROP_DEAF_FRAMES = int(FPS * 0.4)    # a ledge he stepped off cannot catch him
                                     # again while his trailing foot clears it
PANIC_LEVEL = 1.0           # he bolts
PANIC_EXIT = 0.24           # and keeps running until his nerve drops to here
JUMPY_NERVE = 0.52          # what a scare leaves behind, so round two is quicker
PANIC_SPEED = 2.2 * SCALE   # he runs a lot faster than he walks
PANIC_STRIDE = 7 * SCALE
PANIC_MAX_FRAMES = FPS * 10          # he cannot sprint forever
SIT_SWING_SECONDS = 3.6
SIT_REST_SECONDS = 4.0
FLOOR_MARGIN = 2        # used only when no taskbar is found along the bottom
DEBUG_OUTLINE = "--debug" in sys.argv   # python deskbuddy.py --debug
BEACON_SECONDS = 5      # loud startup marker; set to 0 once you have seen him
SELECTION_DRAG_MIN = 18
SHIP_EXIT_FRAMES = int(FPS * 0.25)
SHIP_AWAY_FRAMES = FPS * 6
SHIP_PICKUP_Y = 34 * SCALE
SHIP_RETURN_SPEED = 3.2 * SCALE
ABDUCTION_FOLLOW_MAX = 12 * SCALE

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
    "...SSS...",
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
# Knees and elbows exist only for the physics. They live after the seven drawn
# points so the solver can treat every point alike while self.pts stays the
# seven that the hand-authored poses write to.
KNEE_L, KNEE_R, ELBOW_L, ELBOW_R = range(7, 11)
BODY_POINTS = 11
LIMB_JOINT = {FOOT_L: KNEE_L, FOOT_R: KNEE_R,
              HAND_L: ELBOW_L, HAND_R: ELBOW_R}

THIGH_LEN = SHIN_LEN = LEG_LEN * 0.5
UPPER_ARM_LEN = FOREARM_LEN = ARM_LEN * 0.5

STICKS = [
    (HEAD, CHEST, NECK_LEN, 1.0),
    (CHEST, HIPS, TORSO_LEN, 1.0),
    (HEAD, HIPS, NECK_LEN + TORSO_LEN * 0.94, 0.7),    # keeps the spine honest
    (HIPS, KNEE_L, THIGH_LEN, 1.0),
    (KNEE_L, FOOT_L, SHIN_LEN, 1.0),
    (HIPS, KNEE_R, THIGH_LEN, 1.0),
    (KNEE_R, FOOT_R, SHIN_LEN, 1.0),
    (CHEST, ELBOW_L, UPPER_ARM_LEN, 1.0),
    (ELBOW_L, HAND_L, FOREARM_LEN, 1.0),
    (CHEST, ELBOW_R, UPPER_ARM_LEN, 1.0),
    (ELBOW_R, HAND_R, FOREARM_LEN, 1.0),
    (KNEE_L, KNEE_R, LEG_LEN * 0.26, 0.05),            # legs travel together
    (FOOT_L, FOOT_R, LEG_LEN * 0.45, 0.07),
]

# Ragdoll joint limits. Every angle is measured in the torso's own frame, so
# they tumble with him and mean the same thing upside down.
#   root, joint, tip, span, which way the joint bulges, swing forward,
#   swing back, how far the limb may fold up
RAG_LIMBS = (
    (HIPS, KNEE_L, FOOT_L, LEG_LEN, 1.0,
     math.radians(88), math.radians(32), LEG_LEN * 0.4),
    (HIPS, KNEE_R, FOOT_R, LEG_LEN, 1.0,
     math.radians(88), math.radians(32), LEG_LEN * 0.4),
    (CHEST, ELBOW_L, HAND_L, ARM_LEN, -1.0,
     math.radians(168), math.radians(58), ARM_LEN * 0.35),
    (CHEST, ELBOW_R, HAND_R, ARM_LEN, -1.0,
     math.radians(168), math.radians(58), ARM_LEN * 0.35),
)
NECK_SWING = math.radians(52)   # how far his head can loll off the spine
JOINT_SOFTNESS = 0.55           # how hard a limit corrects in one pass
BEND_BULGE = 0.35 * SCALE       # a hair of bend, so a hinge has a side to fold to
REST_SPEED = 1.1 * SCALE        # slower than this and he settles, not bounces
CONTACT_FRICTION = 0.2          # extra lateral damping while a point is supported
GROUND_DRAG = 0.42              # removes sideways energy constraints add on contact

# The righting reflex. While he is loose he is treated as the inverted
# pendulum a falling person is. Whether he can still save it is decided by
# the capture point, xi = com + v/omega0 with omega0 = sqrt(g/h): the spot his
# weight is really headed for. While that lands inside a step of his feet he
# can catch himself, and once it is past that the fall is committed and no
# effort helps, which is why people flail for a moment and then go down.
# An exponential envelope on top guarantees the effort always runs out.
# Effort is only ever spent bracing toward a pose and killing speed, never
# adding any, so no amount of it can throw him into the air.
RIGHT_KP = 1.15                 # trunk thrown back, per radian of tilt
RIGHT_KD = 4.5                  # and per radian per frame of tilt rate
RIGHT_SHIFT = 0.42              # cap on that, as a fraction of his own height
RIGHT_TAU = FPS * 1.0           # e-folding time of the effort envelope, frames
RIGHT_SHARP = 6.0               # how abruptly authority dies past tipping point
RIGHT_SMOOTH = 0.34             # lag on the sensed tilt rate, as real reflexes have
RIGHT_SPIN = 0.085              # tumbling faster than this and he cannot find up
RIGHT_DOWN = math.radians(70)   # leant further than this and he is not standing
RIGHT_IMPACT = 11 * SCALE       # landing faster than this buckles him anyway
RIGHT_TONE = 0.28               # how far toward the braced pose he gets in a frame
RIGHT_BRACE = 0.22              # how much of his speed stiff muscles kill per frame
RIGHT_AIR = 0.4                 # authority left with nothing under his feet
RIGHT_STEP = LEG_LEN * 0.6      # furthest he can plant a foot from under himself
RIGHT_STAGGER = LEG_LEN * 1.5   # and how much stepping he has in him all told
RIGHT_UPRIGHT = math.radians(14)    # close enough to vertical to count as saved
RIGHT_STEADY = math.radians(1.3)    # and slow enough, per frame
RIGHT_CATCH = int(FPS * 0.4)    # held like that this long and he has it back
RIGHT_GIVE_UP = 0.05            # below this the reflex is spent and he is limp

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
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetWindow.restype = wt.HWND

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
SWP_NOZORDER, SWP_FRAMECHANGED = 0x0004, 0x0020
VK_LBUTTON = 0x01
GW_HWNDNEXT = 2

SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_Ghost",
    "TaskListThumbnailWnd", "ForegroundStaging", "XamlExplorerHostIslandWindow",
}


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def is_desktop_surface(point, self_hwnd=0):
    """Whether a point belongs to Explorer's desktop window hierarchy."""
    hwnd = user32.WindowFromPoint(wt.POINT(point.x(), point.y()))
    if hwnd == self_hwnd:
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    for _ in range(5):
        if not hwnd:
            return False
        if _class_name(hwnd) in {"Progman", "WorkerW", "SysListView32"}:
            return True
        hwnd = user32.GetParent(hwnd)
    return False


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

WALK, IDLE, SIT, SLEEP, GRABBED, FREE, STUNNED, CLIMB, PANIC, WARY, ABDUCT, ABOARD = range(12)
CLIMB_SPEED = 0.55 * SCALE
CLIMB_CHANCE = 0.55        # odds he takes a wall rather than walking past it
WALK_STRIDE = 5 * SCALE
CLIMB_STRIDE = 2.5 * SCALE

# Headroom. Above STAND_HEIGHT he walks upright; at CRAWL_HEIGHT he is flat out
# on hands and knees, and in between he blends between the two.
STAND_HEIGHT = LEG_LEN + TORSO_LEN + NECK_LEN + RADII[HEAD]
CRAWL_HEIGHT = 15 * SCALE
CROUCH_RATE = 0.08         # how fast the crouch blend follows the ceiling
CRAWL_SLOWDOWN = 0.55      # fraction of walk speed and stride lost at full crawl


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
        self.body = [Pt() for _ in range(BODY_POINTS)]
        self.pts = self.body[:7]   # the same objects; the ones poses drive
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
        self.crouch = 0.0          # 0 upright, 1 flat on hands and knees
        self.free_frames = 0
        self.blink = 0
        self.look = (0.0, 0.0)
        self.grab_idx = None
        self.spin = 0.0
        self.balance = 0.0         # 1 fighting the fall with everything, 0 limp
        self.tilt_prev = None      # last frame's lean, for the damping term
        self.tilt_rate = 0.0       # smoothed lean rate he actually reacts to
        self.caught = 0            # frames he has held himself upright and still
        self.step_to = None        # spot the catching foot is committed to
        self.step_foot = FOOT_R
        self.stagger = 0.0         # stepping he has left before the fall wins
        self.support = [None] * BODY_POINTS  # ledge a point landed on this frame
        self.ground_contact = False
        self.allow_perch = True
        self.allow_panic = SPOOK_ENABLED
        self.spook = 0.0           # nerve, 0 calm to PANIC_LEVEL and he bolts
        self.calm = 0              # frames where nothing can spook him
        self.panic_frames = 0
        self.panic_turn = 0        # cooldown so he does not jitter when cornered
        self.teeter = 0            # frames spent backed up against a drop
        self.drop_hwnd = None      # ledge he just walked off, and how much
        self.drop_frames = 0       # longer it stays deaf to him
        self.cursor_near = 0.0     # 0 outside SPOOK_RADIUS, 1 right on top of him
        self.cursor_prev = None
        self.trail = []            # cursor samples, for throw velocity
        self.pose_state = self.state
        self.pose_from = None
        self.pose_frame = 0
        self.abduct_target = None
        self.abduct_follow_speed = 0.0
        self.set_pose(self.anchor_x, floor_y)

    # -- pose ------------------------------------------------------------

    def ground_y(self):
        if self.state == CLIMB:
            return self.climb_y
        if self.ledge:
            return self.ledge["y"]
        return self.floor_y

    def headroom(self):
        return self.ground_y() - self.bounds.top()

    def crouch_target(self):
        """0 where he can stand, 1 where the ceiling forces him onto all fours."""
        span = STAND_HEIGHT - CRAWL_HEIGHT
        return max(0.0, min(1.0, (STAND_HEIGHT - self.headroom()) / span))

    def crawl_pose(self, ax, gy, f, ph):
        """Hands and knees, low enough to clear a ceiling CRAWL_HEIGHT above."""
        hip_y = gy - 5.5 * SCALE
        chest_x = ax + f * TORSO_LEN * 0.8
        chest_y = gy - 7.2 * SCALE
        pose = {
            HIPS: (ax, hip_y),
            CHEST: (chest_x, chest_y),
            HEAD: (chest_x + f * 3 * SCALE, chest_y - 2.8 * SCALE),
        }
        stride = WALK_STRIDE * (1.0 - CRAWL_SLOWDOWN)
        for hand, foot, offset in ((HAND_R, FOOT_L, 0.0),
                                   (HAND_L, FOOT_R, math.pi)):
            step, lift = step_cycle(ph + offset, stride, 1.8 * SCALE)
            pose[hand] = (chest_x + f * (2.5 * SCALE + step), gy)
            pose[foot] = (ax + f * (step - 5 * SCALE), gy - lift - SCALE)
        return pose

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
        self.seed_joints()

    def body_frame(self):
        """Unit vectors up and forward along his torso, whatever way up he is."""
        chest, hips = self.pts[CHEST], self.pts[HIPS]
        ux, uy = chest.x - hips.x, chest.y - hips.y
        d = math.hypot(ux, uy) or 1.0
        ux, uy = ux / d, uy / d
        return (ux, uy), (-uy * self.facing, ux * self.facing)

    def seed_joints(self):
        """Park the knees and elbows where the drawing already implies they are,
        so handing a pose over to the physics never snaps a limb."""
        _up, fwd = self.body_frame()
        for root, joint, tip, span, bulge, _f, _b, _fold in RAG_LIMBS:
            a, b_ = self.body[root], self.body[tip]
            dx, dy = b_.x - a.x, b_.y - a.y
            d = math.hypot(dx, dy) or 0.0001
            reach = min(d, span)
            out = max(BEND_BULGE,
                      math.sqrt(max(0.0, (span * 0.5) ** 2 - (reach * 0.5) ** 2)))
            nx, ny = -dy / d, dx / d
            if (nx * fwd[0] + ny * fwd[1]) * bulge < 0:
                nx, ny = -nx, -ny
            self.body[joint].teleport((a.x + b_.x) / 2 + nx * out,
                                      (a.y + b_.y) / 2 + ny * out)

    def joint_of(self, tip):
        """The simulated knee or elbow for a limb, while the physics is running."""
        if self.state not in (GRABBED, FREE, STUNNED):
            return None
        return self.body[LIMB_JOINT[tip]]

    def animate(self):
        """Kinematic poses. Physics is off; we just place the points."""
        p = self.pts
        ax, gy = self.anchor_x, self.ground_y()
        f = self.facing
        ph = self.phase

        if self.state != self.pose_state:
            if self.state == SIT:
                self.sit_time = 0.0
            settled = (WALK, IDLE, SIT, SLEEP, PANIC, WARY)
            if self.state in settled and self.pose_state in settled:
                self.pose_from = [(point.x - ax, point.y - gy) for point in p]
                self.pose_frame = 0
            else:
                self.pose_from = None
            self.pose_state = self.state

        if self.state in (WALK, IDLE, PANIC, WARY):
            run = self.state == PANIC
            if run:
                stride, lift, swing_amt, lean = PANIC_STRIDE, 4.6 * SCALE, 1.0, f * 2.6
            elif self.state == WARY:
                stride, lift, swing_amt, lean = WARY_STRIDE, 1.1 * SCALE, 0.5, -f * 1.5
            elif self.state == WALK:
                stride, lift, swing_amt, lean = WALK_STRIDE, 3.2 * SCALE, 1.0, f
            else:
                stride, lift, swing_amt, lean = WALK_STRIDE, 3.2 * SCALE, 0.0, 0.0
            swing = math.cos(ph) * swing_amt
            bob = math.cos(ph) ** 2 * 0.65 * SCALE * swing_amt if swing_amt else \
                math.sin(ph * 0.35) * 0.5 * SCALE
            hip_y = gy - LEG_LEN * (0.86 if run else 0.92) + bob
            chest_x = ax + lean * 0.6 * SCALE
            chest_y = hip_y - TORSO_LEN
            arm = 3.4 * SCALE
            hang = ARM_LEN * 0.72
            pose = {
                HIPS: (ax, hip_y),
                CHEST: (chest_x, chest_y),
                HEAD: (ax + lean * 1.1 * SCALE, chest_y - NECK_LEN),
                HAND_L: (chest_x - swing * arm * f, chest_y + hang),
                HAND_R: (chest_x + swing * arm * f, chest_y + hang),
            }
            for foot, offset in ((FOOT_L, 0.0), (FOOT_R, math.pi)):
                step, foot_lift = step_cycle(ph + offset, stride, lift)
                pose[foot] = (ax + step * f, gy - foot_lift)
            if run:
                # arms straight up, flailing - the universal sign of panic
                flail = math.sin(ph * 1.7) * 1.8 * SCALE
                pose[HAND_L] = (chest_x - f * 1.6 * SCALE + flail,
                                chest_y - ARM_LEN * 0.78 + abs(flail) * 0.25)
                pose[HAND_R] = (chest_x - f * 3.2 * SCALE - flail,
                                chest_y - ARM_LEN * 0.9 - abs(flail) * 0.25)
            elif self.state == WARY:
                # hands up between him and the cursor, ready to leg it
                guard = (0.6 + 0.3 * math.sin(ph * 0.8)) * SCALE
                pose[HAND_L] = (chest_x + f * 2.4 * SCALE,
                                chest_y + hang * 0.3 - guard)
                pose[HAND_R] = (chest_x + f * 3.4 * SCALE,
                                chest_y + hang * 0.1 - guard)
            if self.state == IDLE:
                pose[FOOT_L] = (ax - 2 * SCALE, gy)
                pose[FOOT_R] = (ax + 2 * SCALE, gy)
                pose[HAND_L] = (ax - 4 * SCALE, chest_y + hang)
                pose[HAND_R] = (ax + 4 * SCALE, chest_y + hang)
            if self.crouch > 0.001:
                crawl = self.crawl_pose(ax, gy, f, ph)
                for i, (tx, ty) in crawl.items():
                    sx, sy = pose[i]
                    px = sx + (tx - sx) * self.crouch
                    py = sy + (ty - sy) * self.crouch
                    if i in (HAND_L, HAND_R):
                        chest_x, chest_y = pose[CHEST]
                        if math.hypot(px - chest_x, gy - chest_y) <= ARM_LEN:
                            py = gy
                    pose[i] = (px, py)
            for i, (px_, py_) in pose.items():
                p[i].place(px_, py_)

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

        elif self.state == ABDUCT:
            tx, ty = self.abduct_target or (self.anchor_x, self.ground_y())
            bob = math.sin(ph * 1.4) * 1.2 * SCALE
            pose = {
                HIPS: (tx, ty + 2 * SCALE + bob),
                CHEST: (tx, ty - TORSO_LEN + bob),
                HEAD: (tx, ty - TORSO_LEN - NECK_LEN + bob),
                HAND_L: (tx - 4 * SCALE, ty - TORSO_LEN * 0.35 + bob),
                HAND_R: (tx + 4 * SCALE, ty - TORSO_LEN * 0.2 + bob),
                FOOT_L: (tx - 3 * SCALE, ty + LEG_LEN * 0.55 + bob),
                FOOT_R: (tx + 3 * SCALE, ty + LEG_LEN * 0.65 + bob),
            }
            max_step = min(ABDUCTION_FOLLOW_MAX,
                           1.5 * SCALE + self.abduct_follow_speed)
            for index, (target_x, target_y) in pose.items():
                dx = target_x - p[index].x
                dy = target_y - p[index].y
                distance = math.hypot(dx, dy)
                step = min(distance, max_step,
                           max(distance * 0.08, self.abduct_follow_speed))
                if distance > 0.001:
                    p[index].place(p[index].x + dx / distance * step,
                                   p[index].y + dy / distance * step)

        if self.pose_from is not None:
            self.pose_frame += 1
            progress = min(1.0, self.pose_frame / (FPS * 0.3))
            blend = progress * progress * (3.0 - 2.0 * progress)
            for point, (start_x, start_y) in zip(p, self.pose_from):
                point.place((ax + start_x) * (1.0 - blend) + point.x * blend,
                            (gy + start_y) * (1.0 - blend) + point.y * blend)
            if progress >= 1.0:
                self.pose_from = None

        self.seed_joints()

    # -- physics ----------------------------------------------------------

    def integrate(self):
        # Reuse the list instead of reallocating it every frame - this runs
        # at 60fps while ragdolling, and a fresh list each call was the
        # single biggest source of transient garbage in the physics step.
        support = self.support
        for i in range(len(support)):
            support[i] = None
        self.ground_contact = False
        for pt in self.body:
            vx = (pt.x - pt.px) * AIR_DRAG
            vy = (pt.y - pt.py) * AIR_DRAG
            pt.px, pt.py = pt.x, pt.y
            pt.x += vx
            pt.y += vy + GRAVITY
        if self.spin:
            cx = sum(p.x for p in self.pts) / 7
            cy = sum(p.y for p in self.pts) / 7
            for pt in self.body:
                dx, dy = pt.x - cx, pt.y - cy
                pt.x += -dy * self.spin
                pt.y += dx * self.spin
            self.spin *= 0.94
            if abs(self.spin) < 0.001:
                self.spin = 0.0

    def solve(self):
        for _ in range(CONSTRAINT_PASSES):
            self.sticks()
            self.limit_joints()
            self.untangle()
            self.collide()
        # bones twice at the end, so nothing a limit nudged is left stretched
        # on screen for the frame
        self.sticks()
        self.sticks()
        self.collide()
        if self.ground_contact:
            for pt in self.body:
                pt.px = pt.x - pt.vx * GROUND_DRAG

    def sticks(self):
        for a, b, rest, stiff in STICKS:
            pa, pb = self.body[a], self.body[b]
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

    # -- joint limits -----------------------------------------------------

    def limit_joints(self):
        """Hips, shoulders, neck and the two hinges. Without these he folds
        through himself and settles in shapes a body cannot make."""
        up, fwd = self.body_frame()
        down = (-up[0], -up[1])
        held = self.grab_idx
        if held not in (HEAD, CHEST):
            self._swing(self.body[CHEST], (self.body[HEAD],), up, fwd,
                        NECK_SWING, NECK_SWING)
        for root, joint, tip, _span, bulge, swing_f, swing_b, fold in RAG_LIMBS:
            a, j, t = self.body[root], self.body[joint], self.body[tip]
            self._hinge(a, j, t, fwd, bulge)
            if held == root or held == tip:
                continue      # the hand of his you are holding stays put
            self._swing(a, (j, t), down, fwd, swing_f, swing_b)
            self._unfold(a, t, fold)

    def _swing(self, root, movers, axis, fwd, limit_fwd, limit_back):
        """Swing a whole limb back into the cone it is allowed to reach."""
        lead = movers[0]
        vx, vy = lead.x - root.x, lead.y - root.y
        if abs(vx) < 0.0001 and abs(vy) < 0.0001:
            return
        angle = math.atan2(vx * fwd[0] + vy * fwd[1],
                           vx * axis[0] + vy * axis[1])
        clamped = max(-limit_back, min(limit_fwd, angle))
        if clamped == angle:
            return
        # the (axis, fwd) basis flips handedness with his facing, so the world
        # rotation that closes the gap flips with it too
        handed = axis[0] * fwd[1] - axis[1] * fwd[0]
        turn = (clamped - angle) * handed * JOINT_SOFTNESS
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        for pt in movers:
            dx, dy = pt.x - root.x, pt.y - root.y
            pt.x = root.x + dx * cos_t - dy * sin_t
            pt.y = root.y + dx * sin_t + dy * cos_t

    def _hinge(self, root, joint, tip, fwd, bulge):
        """Knees fold forward and elbows back, never the other way round."""
        dx, dy = tip.x - root.x, tip.y - root.y
        d = math.hypot(dx, dy)
        if d < 0.0001:
            return
        nx, ny = -dy / d, dx / d
        if (nx * fwd[0] + ny * fwd[1]) * bulge < 0:
            nx, ny = -nx, -ny
        out = ((joint.x - (root.x + tip.x) / 2) * nx +
               (joint.y - (root.y + tip.y) / 2) * ny)
        if out < BEND_BULGE:
            push = (BEND_BULGE - out) * JOINT_SOFTNESS
            joint.x += nx * push
            joint.y += ny * push

    def _unfold(self, a, b_, least):
        """A limb can fold, but not flat back on itself."""
        dx, dy = b_.x - a.x, b_.y - a.y
        d = math.hypot(dx, dy)
        if d >= least:
            return
        if d < 0.0001:
            dx, dy, d = 0.0, least, least
        push = (least - d) / d * 0.5 * JOINT_SOFTNESS
        a.x -= dx * push
        a.y -= dy * push
        b_.x += dx * push
        b_.y += dy * push

    def untangle(self):
        """Keep his legs out of his own head and chest. Arms are left alone -
        this is a side view, so an arm lying across the torso is just an arm
        in front of him, and shoving it clear only makes him look winged."""
        chest, head = self.body[CHEST], self.body[HEAD]
        for i in (FOOT_L, FOOT_R, KNEE_L, KNEE_R):
            if i == self.grab_idx:
                continue
            pt = self.body[i]
            self._clear_ball(pt, head, RADII[HEAD] * 0.9)
            self._clear_ball(pt, chest, RADII[CHEST])

    def _clear_ball(self, pt, centre, radius):
        ox, oy = pt.x - centre.x, pt.y - centre.y
        d = math.hypot(ox, oy)
        if d >= radius or d < 0.0001:
            return
        push = (radius - d) / d * JOINT_SOFTNESS
        pt.x += ox * push
        pt.y += oy * push

    def collide(self):
        left, right = self.bounds.left(), self.bounds.right()
        floor = self.floor_y
        for i, pt in enumerate(self.body):
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
                top, x0, x1, damped = held
                if x0 < pt.x < x1:
                    self.ground_contact = True
                    if pt.y > top:
                        pt.y = top
                    if not damped:
                        pt.px = pt.x - pt.vx * CONTACT_FRICTION
                        self.support[i] = (top, x0, x1, True)
                    continue
                self.support[i] = None

            landed = False
            for lg in self.ledges:
                if self.drop_frames > 0 and lg["hwnd"] == self.drop_hwnd:
                    continue
                top = lg["y"] - r
                if not (lg["x0"] < pt.x < lg["x1"]):
                    continue
                # catch him crossing downward, or sinking after the solver
                # pulled him under
                if pt.py <= top + 1 and pt.y > top:
                    self._land(pt, top)
                    self.support[i] = (top, lg["x0"], lg["x1"], False)
                    self.ground_contact = True
                    landed = True
                    break
            if landed:
                continue

            if pt.y > floor - r:
                self._land(pt, floor - r)
                self.ground_contact = True
                continue

            if pt.y < self.bounds.top() + r:
                pt.y = self.bounds.top() + r
                pt.py = pt.y - abs(pt.vy) * BOUNCE

    def _land(self, pt, y):
        vx, vy = pt.vx, pt.vy
        pt.y = y
        if vy > REST_SPEED:
            pt.py = pt.y + vy * BOUNCE
            pt.px = pt.x - vx * FRICTION
        else:
            # too slow to bounce. Letting it rest is what stops a limb
            # buzzing against the floor while the solver argues with gravity.
            pt.py = pt.y
            pt.px = pt.x - vx * FRICTION * 0.6

    def energy(self):
        return sum(abs(p.vx) + abs(p.vy) for p in self.pts)

    # -- balance ----------------------------------------------------------

    def right_self(self, footed):
        """One frame of the righting reflex. See the RIGHT_* constants."""
        hips, head = self.body[HIPS], self.body[HEAD]
        lx, ly = head.x - hips.x, head.y - hips.y
        length = math.hypot(lx, ly) or 1.0
        tilt = math.atan2(lx, -ly)          # 0 upright, signed by the way he goes
        raw = 0.0 if self.tilt_prev is None else \
            (tilt - self.tilt_prev + math.pi) % math.tau - math.pi
        self.tilt_prev = tilt
        # A reflex reads a filtered, slightly stale rate, not the solver's
        # frame-to-frame twitch, so his effort ramps instead of strobing.
        rate = self.tilt_rate + (raw - self.tilt_rate) * RIGHT_SMOOTH
        self.tilt_rate = rate

        fl, fr = self.body[FOOT_L], self.body[FOOT_R]
        com_x = sum(p.x for p in self.pts) / 7
        com_y = sum(p.y for p in self.pts) / 7
        com_vx = sum(p.vx for p in self.pts) / 7
        com_vy = sum(p.vy for p in self.pts) / 7
        stand = max(fl.y, fr.y)
        omega0 = math.sqrt(GRAVITY / max(stand - com_y, LEG_LEN * 0.5))
        # Judged in the air as well as on the ground: a body already travelling
        # faster than it can ever step to keep up with is going down, and no
        # amount of bracing on landing is allowed to pretend otherwise. The
        # tolerance is whatever stagger he has left, so the last step he has in
        # him is also the last chance he gets.
        xi = com_x + com_vx / omega0
        out = max(min(fl.x, fr.x) - xi, xi - max(fl.x, fr.x), 0.0)
        reach = max(2.0 * SCALE, min(RIGHT_STEP, self.stagger))
        commit = 1.0 / (1.0 + math.exp(RIGHT_SHARP * (out / reach - 1.0)))
        # Never climbs: once the capture point is away the fall is committed,
        # and the envelope only ever runs down from there.
        self.balance = min(self.balance,
                           commit
                           * math.exp(-self.free_frames / RIGHT_TAU)
                           * max(0.0, 1.0 - abs(tilt) / RIGHT_DOWN)
                           * max(0.0, 1.0 - abs(self.spin) / RIGHT_SPIN)
                           * max(0.0, 1.0 - max(0.0, com_vy) / RIGHT_IMPACT))
        if self.balance < RIGHT_GIVE_UP:
            self.balance = 0.0
            self.caught = 0
            return

        # Hip strategy: the trunk is thrown back against the fall and the hips
        # go the other way, which shifts his weight without his feet moving.
        shift = -(RIGHT_KP * math.sin(tilt) + RIGHT_KD * rate) * self.balance
        shift = max(-RIGHT_SHIFT, min(RIGHT_SHIFT, shift)) * length

        # The pose is built over his feet, which friction holds in place, so
        # bracing pulls his weight back above his base instead of sliding it
        # along the floor. Only the stepping foot travels.
        hx = (fl.x + fr.x) / 2 if footed else hips.x
        gy = stand if footed else hips.y + LEG_LEN   # where his feet belong
        hy = gy - LEG_LEN
        cy = hy - TORSO_LEN
        knee_y = hy + THIGH_LEN * 0.92
        bulge = self.facing * BEND_BULGE * 2

        # The capture step: a foot goes for the ground under where his weight is
        # headed, as far as his legs reach and no further. The spot is latched
        # until he gets there, because a step is a commitment, not a drift, and
        # he only has so much stagger in him before there is nowhere left to go.
        catch = max(hx - RIGHT_STEP, min(hx + RIGHT_STEP,
                                         com_x + com_vx / omega0))
        if self.step_to is None \
                or abs(self.body[self.step_foot].x - self.step_to) < 2 * SCALE \
                or abs(self.step_to - hx) > RIGHT_STEP:
            stride = math.copysign(min(abs(catch - hx), self.stagger), catch - hx)
            self.step_to = hx + stride
            self.step_foot = FOOT_R if stride > 0 else FOOT_L
            self.stagger -= abs(stride)
        lead = self.step_foot
        trail = FOOT_L if lead == FOOT_R else FOOT_R

        grip = RIGHT_TONE * self.balance
        brace = RIGHT_BRACE * self.balance
        if not footed:
            grip *= RIGHT_AIR
            brace *= RIGHT_AIR

        # Applied directly instead of via a dict of target points - this runs
        # every ragdoll frame, and building and iterating a fresh dict and
        # its tuples here was a steady source of transient garbage.
        def brace_toward(i, tx, ty):
            pt = self.body[i]
            vx, vy = pt.vx, pt.vy
            pt.x += (tx - pt.x) * grip
            pt.y += (ty - pt.y) * grip
            # Bracing spends the speed he already has; it never hands him any,
            # so no amount of effort can fling him off the floor.
            pt.px = pt.x - vx * (1.0 - brace)
            pt.py = pt.y - vy * (1.0 - brace)

        # hips counter the trunk, so the weight moves but the pose does not
        brace_toward(HIPS, hx - shift * 0.35, hy)
        brace_toward(CHEST, hx + shift, cy)
        brace_toward(HEAD, hx + shift * 1.2, cy - NECK_LEN)
        brace_toward(ELBOW_L, hx + shift - ARM_LEN * 0.42, cy + ARM_LEN * 0.05)
        brace_toward(ELBOW_R, hx + shift + ARM_LEN * 0.42, cy + ARM_LEN * 0.05)
        brace_toward(HAND_L, hx + shift - ARM_LEN * 0.72, cy - ARM_LEN * 0.3)
        brace_toward(HAND_R, hx + shift + ARM_LEN * 0.72, cy - ARM_LEN * 0.3)
        brace_toward(KNEE_L, (hx + fl.x) / 2 + bulge, knee_y)
        brace_toward(KNEE_R, (hx + fr.x) / 2 + bulge, knee_y)
        brace_toward(lead, self.step_to, gy)
        brace_toward(trail, self.body[trail].x, gy)

        if footed and abs(tilt) < RIGHT_UPRIGHT and abs(rate) < RIGHT_STEADY:
            self.caught += 1
        else:
            self.caught = 0

    # -- state changes ----------------------------------------------------

    def go_free(self, vx=0.0, vy=0.0, spin=0.0):
        self.state = FREE
        self.pose_state = FREE
        self.pose_from = None
        self.grab_idx = None
        self.spin = spin
        self.free_frames = 0
        self.stun = 0
        self.teeter = 0
        self.balance = 1.0
        self.tilt_prev = None
        self.tilt_rate = 0.0
        self.caught = 0
        self.step_to = None
        self.stagger = RIGHT_STAGGER
        self.seed_joints()
        for pt in self.body:
            pt.kick(vx + random.uniform(-0.6, 0.6),
                    vy + random.uniform(-0.6, 0.6))

    def begin_abduction(self, target):
        """Suspend the buddy at the current selection rectangle's center."""
        if self.state not in (WALK, IDLE, SIT, SLEEP, WARY, PANIC):
            return False
        self.state = ABDUCT
        self.abduct_target = (target.x(), target.y())
        self.abduct_follow_speed = 0.0
        self.pose_from = None
        self.spook = 0.0
        self.calm = FPS
        return True

    def move_abduction(self, target):
        if self.state != ABDUCT:
            return
        next_target = (target.x(), target.y())
        old_target = self.abduct_target or next_target
        self.abduct_follow_speed = math.hypot(next_target[0] - old_target[0],
                                               next_target[1] - old_target[1]) * 1.1
        self.abduct_target = next_target

    def end_abduction(self):
        if self.state != ABDUCT:
            return False
        self.abduct_target = None
        self.abduct_follow_speed = 0.0
        self.go_free(0.0, 2.0 * SCALE)
        return True

    def board_ship(self):
        if self.state != ABDUCT:
            return False
        self.state = ABOARD
        self.abduct_target = None
        self.abduct_follow_speed = 0.0
        return True

    def begin_unloading(self, target):
        self.state = ABDUCT
        self.abduct_target = (target.x(), target.y())
        self.abduct_follow_speed = 0.0
        self.pose_from = None

    def return_home(self, x):
        self.ledge = next((ledge for ledge in self.ledges if ledge["hwnd"] == -1),
                          None)
        self.wall = None
        self.anchor_x = min(max(x, self.walk_min()), self.walk_max())
        self.state = WALK
        self.timer = 200
        self.set_pose(self.anchor_x, self.ground_y())

    def step_off(self, vx):
        """Walk clean off the ledge he is on, carrying his speed with him.

        No upward kick: he does not hop off an edge, he just stops having
        anything under him. The ledge goes deaf for a moment so the foot he
        has not swung past the edge yet cannot land back on it and tip him
        over backwards.
        """
        if self.ledge is not None:
            self.drop_hwnd = self.ledge["hwnd"]
            self.drop_frames = DROP_DEAF_FRAMES
            self.ledge = None
        self.go_free(vx, 0.0, spin=math.copysign(random.uniform(0.02, 0.05), vx))

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
        self.spook = 0.0
        self.calm = int(FPS * 1.2)
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

    def wall_hit(self):
        """A window edge right in front of him at running speed - a face full."""
        gy = self.ground_y()
        for w in self.walls:
            if self.ledge and w["hwnd"] == self.ledge["hwnd"]:
                continue
            distance = (w["x"] - self.anchor_x) * self.facing
            if not -PANIC_SPEED <= distance <= 4 * SCALE + PANIC_SPEED:
                continue
            if w["side"] * self.facing >= 0:
                continue
            # the edge has to actually be in his way, not above his head
            if w["y1"] < gy - 4 * SCALE or w["y0"] > gy - 6 * SCALE:
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

    def wary_speed(self):
        return WARY_SPEED * (0.4 + min(1.0, self.spook))

    def sense_cursor(self, cx, cy):
        """Feed the nerve meter from how hard the cursor is closing on him.

        Both distances are measured from where he is now, so his own running
        never counts as the cursor gaining on him.
        """
        bx, by = self.pts[CHEST].x, self.pts[CHEST].y
        if self.cursor_prev is None:
            self.cursor_prev = (cx, cy)
        d = math.hypot(cx - bx, cy - by)
        was = math.hypot(self.cursor_prev[0] - bx, self.cursor_prev[1] - by)
        moved = math.hypot(cx - self.cursor_prev[0], cy - self.cursor_prev[1])
        self.cursor_prev = (cx, cy)

        if self.calm > 0:          # dusting himself off; deaf to the cursor
            self.calm -= 1
            self.spook = 0.0
            self.cursor_near = 0.0
            return

        if not self.allow_panic:
            self.spook = 0.0
            self.cursor_near = 0.0
            return

        near = max(0.0, 1.0 - d / SPOOK_RADIUS)
        self.cursor_near = near
        if self.state in (WALK, IDLE, SIT, SLEEP, WARY, PANIC):
            self.spook += max(0.0, was - d) * near * SPOOK_GAIN
            if near > 0.55 and moved > 0.5:
                self.spook += SPOOK_LOOM * near
        self.spook = max(0.0, min(1.5, self.spook - SPOOK_DECAY))

    def update(self, cursor):
        if self.state == ABOARD:
            return
        goal = self.crouch_target() if self.state in (WALK, IDLE, PANIC, WARY) \
            else 0.0
        self.crouch += max(-CROUCH_RATE, min(CROUCH_RATE, goal - self.crouch))
        if self.state == WALK:
            self.phase += WALK_SPEED * math.pi / (2 * WALK_STRIDE)
        elif self.state == PANIC:
            self.phase += PANIC_SPEED * math.pi / (2 * PANIC_STRIDE)
        elif self.state == WARY:
            # backing up, so the step cycle runs the other way round
            self.phase -= self.wary_speed() * math.pi / (2 * WARY_STRIDE)
        elif self.state == CLIMB:
            self.phase += CLIMB_SPEED * math.pi / (2 * CLIMB_STRIDE)
        else:
            self.phase += 0.06
        if self.state == SIT:
            self.sit_time += 1.0 / FPS
        self.timer -= 1
        if self.drop_frames > 0:
            self.drop_frames -= 1
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

        cx, cy = cursor.x(), cursor.y()
        self.sense_cursor(cx, cy)
        if self.state in (WALK, IDLE, SIT, SLEEP, WARY):
            if self.spook >= PANIC_LEVEL:
                self.state = PANIC
                self.panic_frames = 0
                self.panic_turn = 0
                self.facing = 1 if cx < self.anchor_x else -1
            elif self.state != WARY and self.spook >= WARY_LEVEL:
                self.state = WARY
            elif self.state == WARY and self.spook < WARY_LEVEL * 0.55:
                self.state = WALK
                self.timer = random.randint(90, 220)
                # WARY leaves him facing the cursor (he backs away from it);
                # keep walking away instead of turning straight back into it
                self.facing = 1 if cx < self.anchor_x else -1

        if self.state == GRABBED:
            pt = self.pts[self.grab_idx]
            pt.place(cursor.x(), cursor.y())
            self.integrate()
            pt.teleport(cursor.x(), cursor.y())
            self.solve()
            return

        if self.state in (FREE, STUNNED):
            footed = self.ground_contact      # integrate() clears it
            self.integrate()
            if self.state == FREE:
                self.right_self(footed)
            self.solve()
            if self.state == FREE:
                self.free_frames += 1
                if self.caught > RIGHT_CATCH:
                    self.stand_up()   # he got his feet back under him
                    return
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

        if self.state == WARY:
            # faces the cursor and gives ground, and on a window he runs out of
            # ledge before he runs out of nerve
            self.facing = 1 if cx > self.anchor_x else -1
            want = self.anchor_x - self.facing * self.wary_speed()
            self.anchor_x = min(max(want, self.walk_min()), self.walk_max())
            if abs(want - self.anchor_x) > 0.01 and self.cursor_near > 0.0:
                # nowhere left to go, but only while the cursor is still a threat
                self.spook += SPOOK_CORNERED * self.cursor_near
                self.teeter += 1
                if self.ledge and self.ledge["hwnd"] != -1 \
                        and self.teeter > TEETER_FRAMES:
                    self.step_off(-self.facing * self.wary_speed())
                    self.spook = JUMPY_NERVE
                    return
            else:
                self.teeter = 0
            self.animate()
            return

        if self.state == PANIC:
            self.panic_frames += 1
            if self.spook < PANIC_EXIT or self.panic_frames > PANIC_MAX_FRAMES:
                self.state = WARY           # stops, panting, still watching you
                self.spook = max(self.spook, JUMPY_NERVE)
                self.animate()
                return
            self.panic_turn -= 1
            if (cx - self.anchor_x) * self.facing > 0 and self.panic_turn <= 0:
                self.facing *= -1           # cursor got in front of him
                self.panic_turn = int(FPS * 0.4)
            self.anchor_x += (PANIC_SPEED * (1.0 - CRAWL_SLOWDOWN * self.crouch)
                              * self.facing)
            w = self.wall_hit()
            if w is not None:               # straight into the side of a window
                self.anchor_x = w["x"] + w["side"] * 2 * SCALE
                self.go_free(-self.facing * PANIC_SPEED * 1.3,
                             -PANIC_SPEED * 1.1,
                             spin=-self.facing * random.uniform(0.05, 0.1))
                self.spook = JUMPY_NERVE
                return
            lo, hi = self.walk_min(), self.walk_max()
            if self.anchor_x < lo or self.anchor_x > hi:
                self.anchor_x = min(max(self.anchor_x, lo), hi)
                if self.ledge:              # runs clean off the ledge
                    self.step_off(PANIC_SPEED
                                  * (1.0 - CRAWL_SLOWDOWN * self.crouch)
                                  * self.facing)
                    self.spook = JUMPY_NERVE
                    return
                self.facing *= -1           # cornered against the screen edge
                self.panic_turn = int(FPS * 0.4)
            self.animate()
            return

        if self.state == WALK:
            self.anchor_x += (WALK_SPEED * (1.0 - CRAWL_SLOWDOWN * self.crouch)
                              * self.facing)
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
                    self.step_off(WALK_SPEED
                                  * (1.0 - CRAWL_SLOWDOWN * self.crouch)
                                  * self.facing)   # steps off on purpose
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
        if self.crouch > 0.35:
            # no room to sit up or stretch out, so he just keeps crawling
            self.timer = random.randint(160, 380)
            return
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


class DesktopSelection:
    """A desktop-only drag tracker kept independent of Qt mouse delivery."""
    def __init__(self):
        self.origin = None
        self.was_down = False
        self.triggered = False

    def update(self, down, cursor, desktop):
        if down and not self.was_down:
            self.origin = QPoint(cursor) if desktop else None
            self.triggered = False
        self.was_down = down
        if not down:
            self.origin = None
            return None
        if self.origin is None:
            return None
        rect = QRect(self.origin, cursor).normalized()
        if rect.width() < SELECTION_DRAG_MIN or rect.height() < SELECTION_DRAG_MIN:
            return None
        return rect


class AlienShip:
    def __init__(self):
        self.rect = None
        self.x = self.y = 0.0
        self.frame = 0
        self.mode = "idle"
        self.bounds = None
        self.pickup_y = 0.0
        self.passenger_x = 0.0
        self.returning = False

    def begin(self, rect, bounds):
        self.rect = QRect(rect)
        self.x = rect.center().x()
        self.bounds = QRect(bounds)
        self.pickup_y = bounds.top() + SHIP_PICKUP_Y
        self.y = self.pickup_y
        self.frame = 0
        self.mode = "hover"
        self.returning = True

    def set_beam_bottom(self, y):
        if self.mode not in ("hover", "unloading"):
            return
        self.rect = QRect(int(self.x - 5 * SCALE), int(y), 10 * SCALE, 1)

    def follow_selection(self, rect):
        if self.mode != "hover":
            return
        self.rect = QRect(rect)
        self.x += (rect.center().x() - self.x) * 0.18

    def can_board(self, buddy):
        head = buddy.pts[HEAD]
        return self.mode == "hover" and \
            abs(head.x - self.x) <= 12 * SCALE and \
            head.y <= self.y + 5 * SCALE

    def depart(self, passenger_x, returning=True):
        self.rect = None
        self.passenger_x = passenger_x
        self.frame = 0
        self.mode = "departing"
        self.returning = returning

    def update(self):
        if self.mode == "idle" or self.bounds is None:
            return False
        self.frame += 1
        if self.mode == "departing":
            self.y -= SHIP_RETURN_SPEED
            if self.y < self.bounds.top() - 20 * SCALE:
                self.mode = "away" if self.returning else "idle"
                self.frame = 0
        elif self.mode == "away" and self.frame >= SHIP_AWAY_FRAMES:
            self.x = self.passenger_x
            self.y = self.bounds.top() - 20 * SCALE
            self.mode = "returning"
        elif self.mode == "returning":
            self.y = min(self.pickup_y, self.y + SHIP_RETURN_SPEED)
            if self.y >= self.pickup_y:
                self.mode = "unloading"
                return "returned"
        return False

    def bounds_rect(self):
        if self.mode in ("idle", "away"):
            return None
        hull = QRect(int(self.x - 18 * SCALE), int(self.y - 8 * SCALE),
                     int(36 * SCALE), int(16 * SCALE))
        return hull if self.rect is None else hull.united(self.rect)


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
        self.selection = DesktopSelection()
        self.ship = AlienShip()
        self.abduction_start_top = 0
        self.abduction_start_target_y = 0
        self.unloading_buddy = False
        self.unload_target_y = 0.0
        self.frames = 0
        self.paints = 0
        self.dirty_prev = None   # his painted area last frame, so it gets erased
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
        spook = QAction("Scared of the cursor", m, checkable=True)
        spook.setChecked(self.buddy.allow_panic)
        spook.toggled.connect(self.set_panic)
        m.addAction(spook)
        m.addSeparator()
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
        left_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        selection = self.selection.update(left_down, cursor,
                                          is_desktop_surface(cursor, self.hwnd()))
        b = self.buddy
        if selection is not None and not self.selection.triggered:
            if selection.intersects(b.bounds_rect(2 * SCALE)) and \
                    b.begin_abduction(selection.center()):
                self.selection.triggered = True
                self.ship.begin(selection, self.virt)
                self.abduction_start_top = selection.top()
                self.abduction_start_target_y = selection.center().y()
        if b.state == ABDUCT:
            if self.unloading_buddy:
                landing_y = self.floor_y - LEG_LEN * 0.65
                self.unload_target_y = min(landing_y,
                                           self.unload_target_y + SHIP_RETURN_SPEED)
                target = QPoint(int(self.ship.x), int(self.unload_target_y))
            elif selection is None:
                if b.end_abduction():
                    self.ship.depart(b.pts[HEAD].x, returning=False)
            else:
                self.ship.follow_selection(selection)
                target = QPoint(int(self.ship.x),
                                int(self.ship.pickup_y + TORSO_LEN + NECK_LEN))
            if b.state == ABDUCT:
                b.move_abduction(target)
        if self.dragging:
            self.buddy.trail.append((cursor.x(), cursor.y()))
            if len(self.buddy.trail) > 6:
                self.buddy.trail.pop(0)
        self.buddy.update(cursor)
        if b.state == ABDUCT and self.unloading_buddy:
            self.ship.set_beam_bottom(max(point.y for point in b.pts) + SCALE)
        if b.state == ABDUCT and not self.unloading_buddy and self.ship.can_board(b):
            if b.board_ship():
                self.ship.depart(b.pts[HEAD].x)
        ship_returned = self.ship.update()
        if b.state == ABOARD and ship_returned == "returned":
            self.unloading_buddy = True
            self.unload_target_y = self.ship.pickup_y + TORSO_LEN + NECK_LEN
            b.begin_unloading(QPoint(int(self.ship.x), int(self.unload_target_y)))
        elif self.unloading_buddy and \
                self.unload_target_y >= self.floor_y - LEG_LEN * 0.65 and \
                abs(b.pts[HIPS].y - (self.unload_target_y + 2 * SCALE)) <= SCALE:
            b.return_home(self.ship.x)
            self.unloading_buddy = False
            self.ship.depart(self.ship.x, returning=False)

        new = self.effect_bounds()
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

        if beacon or DEBUG_OUTLINE or self.frames == FPS * BEACON_SECONDS:
            # The beacon fills the screen, and debug mode paints overlays
            # (floor line, ledges) across the whole width, so both still need
            # a full repaint. The exact frame the beacon ends also needs one
            # last full repaint, to erase it - after that, partial updates
            # would leave it stuck on screen forever.
            self.update()
            self.dirty_prev = None
        else:
            # Repainting the whole primary-monitor-sized transparent overlay
            # every frame at 60fps - regardless of how little of it actually
            # changed - was the main cost driving up GPU/compositor memory
            # and CPU use, most noticeable while ragdolling flings him across
            # a much wider area than his usual walk/idle footprint. Only
            # invalidate where he was and where he now is; Qt clears that
            # region to transparent before paintEvent runs, so nothing is
            # left behind.
            dirty = self.effect_bounds() \
                .translated(-self.virt.left(), -self.virt.top())
            region = dirty if self.dirty_prev is None else dirty.united(self.dirty_prev)
            self.dirty_prev = dirty
            self.update(region)

    def effect_bounds(self):
        bounds = self.buddy.bounds_rect(30 * SCALE)
        ship_bounds = self.ship.bounds_rect()
        return bounds if ship_bounds is None else bounds.united(ship_bounds)

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

    def set_panic(self, on):
        b = self.buddy
        b.allow_panic = on
        b.spook = 0.0
        if not on and b.state in (PANIC, WARY):
            b.state = WALK
            b.timer = random.randint(120, 260)

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
            self.draw_abduction(p)
            self.draw_buddy(p)
        except Exception:
            if self.paint_error is None:
                import traceback
                self.paint_error = traceback.format_exc()
                log("drawing the buddy raised:\n" + self.paint_error)
        p.end()

    def draw_abduction(self, p):
        ship = self.ship
        if ship.mode in ("idle", "away"):
            return
        cx, cy = int(ship.x), int(ship.y)
        if ship.rect is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(105, 255, 177, 48))
            p.drawPolygon(QPolygon([
                QPoint(cx - 8 * SCALE, cy + 4 * SCALE),
                QPoint(cx + 8 * SCALE, cy + 4 * SCALE),
                ship.rect.topRight(),
                ship.rect.bottomRight(),
                ship.rect.bottomLeft(),
                ship.rect.topLeft(),
            ]))
        p.setBrush(QColor(82, 92, 112))
        p.drawEllipse(QPoint(cx, cy), 10 * SCALE, 4 * SCALE)
        p.setBrush(QColor(142, 238, 204))
        p.drawEllipse(QPoint(cx, cy - 3 * SCALE), 4 * SCALE, 3 * SCALE)
        p.setBrush(QColor(255, 220, 90))
        p.drawEllipse(QPoint(cx - 6 * SCALE, cy + SCALE), SCALE, SCALE)
        p.drawEllipse(QPoint(cx + 6 * SCALE, cy + SCALE), SCALE, SCALE)

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
        if b.state == ABOARD:
            return
        pts = b.pts
        chest, hips, head = pts[CHEST], pts[HIPS], pts[HEAD]

        # contact shadow, only when he is settled on something
        if b.state in (WALK, IDLE, SIT, SLEEP, PANIC, WARY):
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

            self.limb(p, hips, pts[back[1]], PALETTE["P"], PALETTE["F"], True,
                      b.joint_of(back[1]))
            self.arm(p, chest, pts[back[0]], b.facing, b.joint_of(back[0]))
            self.part(p, self.torso_pm, chest, hips)
            self.limb(p, hips, pts[front[1]], PALETTE["P"], PALETTE["F"], True,
                      b.joint_of(front[1]))
            self.arm(p, chest, pts[front[0]], b.facing, b.joint_of(front[0]))
        self.draw_head(p, head, chest)

        if b.state == SLEEP:
            self.zzz(p, head)
        if b.state in (PANIC, WARY):
            self.fright(p, head)
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
                     CLIMB: "climb", PANIC: "panic", WARY: "wary"}
            p.setPen(QColor(255, 255, 255))
            p.fillRect(self.virt.left() + 10, self.virt.top() + 10, 430, 96,
                       QColor(0, 0, 0, 190))
            p.drawText(self.virt.left() + 22, self.virt.top() + 34,
                       f"state {names.get(b.state)}   nerve {b.spook:.2f}   "
                       f"head {head.x:.0f},{head.y:.0f}")
            p.drawText(self.virt.left() + 22, self.virt.top() + 58,
                       f"floor y={b.floor_y}   screen {self.virt.width()}x"
                       f"{self.virt.height()}   ledges {len(b.ledges)}   "
                       f"walls {len(b.walls)}")
            p.drawText(self.virt.left() + 22, self.virt.top() + 82,
                       f"box {r.x()},{r.y()} {r.width()}x{r.height()}   "
                       f"clickthrough {self.clickthrough}")

    def arm(self, p, a, b_, bend, joint=None):
        """Two segments with an implied elbow, so it reads as an arm."""
        if joint is not None:
            ex, ey = joint.x, joint.y
        else:
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

    def limb(self, p, a, b_, col, shoe, with_shoe, joint=None):
        if joint is not None:
            ex, ey = joint.x, joint.y
        else:
            dx, dy = b_.x - a.x, b_.y - a.y
            d = math.hypot(dx, dy) or 1
            nx, ny = dy / d * self.buddy.facing, -dx / d * self.buddy.facing
            slack = math.sqrt(max(0.0, LEG_LEN ** 2 - d ** 2)) * 0.5
            ex, ey = (a.x + b_.x) / 2 + nx * slack, (a.y + b_.y) / 2 + ny * slack
            if self.buddy.state in (WALK, IDLE, PANIC, WARY):
                crouch = self.buddy.crouch
                ex += (b_.x + self.buddy.facing * 5 * SCALE - ex) * crouch
                ey += (b_.y + SCALE - ey) * crouch
        p.setPen(QPen(col, int(2.4 * SCALE), Qt.SolidLine, Qt.SquareCap))
        p.drawLine(int(a.x), int(a.y), int(ex), int(ey))
        p.drawLine(int(ex), int(ey), int(b_.x), int(b_.y))
        if with_shoe:
            ang = math.degrees(math.atan2(b_.y - ey, b_.x - ex)) - 90
            if self.buddy.state in (WALK, IDLE, PANIC, WARY):
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

    def fright(self, p, head):
        """Marks over his head: one that grows as he gets nervous, then a
        shaking pair once he bolts."""
        b = self.buddy
        # his arms go up over his head when he runs, so clear those too
        top = min(head.y, b.pts[HAND_L].y, b.pts[HAND_R].y) - 7 * SCALE
        if b.state == PANIC:
            jitter = math.sin(b.phase * 2.3) * SCALE
            marks = [(-9 * SCALE - jitter, 0.0), (9 * SCALE + jitter, 0.0)]
        else:
            grow = min(1.0, (b.spook - WARY_LEVEL * 0.55) * 3.0)
            if grow <= 0.05:
                return
            marks = [(b.facing * 7 * SCALE, (1.0 - grow) * 3 * SCALE)]
        for dx, dy in marks:
            x, y = head.x + dx, top + dy
            p.fillRect(int(x - SCALE // 2), int(y), max(1, SCALE),
                       int(3 * SCALE), PALETTE["O"])
            p.fillRect(int(x - SCALE // 2), int(y + 4 * SCALE), max(1, SCALE),
                       max(1, SCALE), PALETTE["O"])


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
