# Gesture planning and calibration

`pogobot.gestures` is an opt-in, pure planner plus a transport protocol. It does not
change the `Swipe` effects or the current ADB actuator. `adb shell input swipe` cannot
consume the timestamped move samples, so do not wire this executor to that command as a
series of swipes. A `PointerSink` implementation must send one continuous pointer, honor
event order, bound every blocking call and support a cancellation release (`ACTION_CANCEL`).
No such single-pointer backend is included or validated on a phone in this change.

The planner creates a minimum-jerk straight path in normalized screen coordinates. It
checks duration, sample count, screen bounds and analytic peak speed. Endpoint variation
is deterministic for a seed and requires an explicitly supplied target rectangle. It is
disabled by default. A rectangle is a geometric bound only; the caller must establish
that it is a safe interaction area from perception confidence. This is not a ballistic
throw model, so it does not yet claim better catch accuracy or support curve throws.

`Calibration.from_anchors()` fits an axis scale and offset from two known capture-to-device
coordinate pairs. This covers scale, crop and inset differences without allowing axis
rotation. Store the resulting calibration with the logical display size and recalibrate
after a display rotation, size, crop or UI change. Mapping that leaves the display is
rejected rather than clipped silently. The planner duration is contact time; host queue
delay is reported separately by the executor and is not folded into the trajectory.

Preview a plan without touching a device:

```sh
python tools/gesture_preview.py --screen 1080 2340 --start 0.5 0.84 --end 0.5 0.38 --duration-ms 240
```

The JSON trace says `kind: planned`; execution traces report injected pointer events only.
Neither is evidence that Android rendered the gesture as intended or that the game
accepted a throw. Before enabling a transport in gameplay, test event timing, one-pointer
continuity, down/up/cancel cleanup, resolution changes, measured device latency, and throw
outcomes using supervised sessions and held-out comparisons. Natural-looking movement
does not establish reduced account-ban risk.
