# DeskBuddy

A pixel art fella who lives on your taskbar.

He walks along the taskbar, scales the side edges of your real windows to get
up onto their title bars, sits and dangles his legs over the drop, and naps.
Windows that do not reach down near his feet are out of reach, and if one
closes or moves while he is on it he falls. Grab him with the left mouse and
throw him: he goes full ragdoll, tumbles across the screen, bounces off walls
and window edges, lies there stunned, then picks himself up and gets back to
work.

| Idle | Walking | Sitting |
| :--: | :--: | :--: |
| ![Idle](assets/buddy_idle.png) | ![Walking](assets/buddy_walk.png) | ![Sitting](assets/buddy_sit.png) |

| Napping | Climbing |
| :--: | :--: |
| ![Napping](assets/buddy_sleep.png) | ![Climbing](assets/buddy_climb.png) |

## Setup (Windows)

```
pip install PySide6
python deskbuddy.py
```

## Controls

| Action | Effect |
| --- | --- |
| Left click + drag | Grab and throw him |
| Double click | He jumps |
| Right click | Menu (sit, nap, toss, perching toggle, quit) |
| Tray icon | Backup quit if he ever gets stuck |

## Tweaking

Every knob worth turning is in the `CONFIG` block at the top of
[deskbuddy.py](deskbuddy.py). `SCALE` changes his size; `PALETTE` changes his
clothes.

## Can't see him?

He lives on your primary monitor, standing on top of the taskbar. On start he
prints the screen size and the exact line he walks along to
[deskbuddy_log.txt](deskbuddy_log.txt). If he is still nowhere, set
`DEBUG_OUTLINE = True` in the config block to draw a box around him and a
line along his walking surface, or run:

```
python deskbuddy.py --debug
```

## Notes

The overlay is a single click-through window covering the primary monitor.
Its input region is clipped to his body each frame, so clicks anywhere else
go straight to whatever is underneath. He tracks resolution and taskbar
changes at runtime. Fullscreen exclusive games will draw over him - that is
normal and expected.
