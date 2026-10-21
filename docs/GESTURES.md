# Gesture planning and calibration

`pogobot.gestures` is an opt-in planner and `pogobot.actions.ADBGestureTransport` sends a
complete timestamped path to one Android process. The Android injector emits one
continuous pointer through `InputManager.injectInputEvent`, with ordered DOWN, MOVE, UP
or CANCEL events. This path is not wired into the regular `Actuator` effect queue yet, so
the current autoplay behavior still uses its `Swipe` fallback. The injector source is
`tools/gesture/Gesture.java` and the host launcher is in `actions.py`.

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
delay is reported separately by the transport and is not folded into the trajectory.

Preview a plan without touching a device:

```sh
python tools/gesture_preview.py --screen 1080 2340 --start 0.5 0.84 --end 0.5 0.38 --duration-ms 240
```

The JSON trace says `kind: planned`. Neither a planned path nor an injector receipt is
evidence that Android rendered the gesture as intended or that the game accepted a throw.
Before enabling a transport in gameplay, test event timing, one-pointer continuity,
down/up/cancel cleanup, resolution changes, measured device latency, and throw outcomes
using supervised sessions and held-out comparisons. Natural-looking movement does not
establish reduced account-ban risk.

Build the Android dex on a machine with a JDK and Android SDK Build Tools (`d8`):

```sh
bash tools/gesture/build.sh
```

The build script writes `pogobot/vendor/gesture.dex`. The runtime transport pushes that
file to `/data/local/tmp` on first use. A caller can execute a planned path from a worker
thread and set the same cancellation event from its pause/recovery path:

```python
transport = ADBGestureTransport((1080, 2340), serial=device_serial)
receipt = transport.execute(trajectory, calibration, cancel_event,
                            screen_wh=(1080, 2340))
```

The `GestureReceipt` records device-side event timing and the host delay to DOWN. It does
not imply that the game accepted the gesture. Calls can block while the process starts,
so they must stay off the frame loop. This checkout did not have `javac` or `d8` on PATH.
The Java source compiled through the JDK compiler
module, but the dex and device behavior remain unbuilt and unverified here because D8 and
an attached Android device were unavailable.
