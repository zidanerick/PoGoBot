# Supervised Android injector smoke test

This procedure tests the injector against the included disposable touch logger, not
Pokémon GO. It does not authorize gameplay wiring. Keep the transport outside the
gameplay action queue until the Technical Lead accepts the shared action, cancellation,
and identity contracts and the integration gate.

## What the current implementation reports

| Condition | Current transport behavior | Required result |
| --- | --- | --- |
| Cancellation already set before `execute()` | Returns `cancelled` without starting ADB or submitting a plan | No touch event |
| Cancellation after injector startup | Sends `CANCEL`; injector attempts `ACTION_CANCEL` and emits `CANCELLED` | No `UP` after cancellation; host receipt is `cancelled` |
| Android rejects an injected event | Injector attempts `ACTION_CANCEL` if a press may be active, then emits `ERROR`; host raises `RuntimeError` | Never report `executed`; no stuck pointer |
| No `READY` before startup deadline | Host raises startup `TimeoutError` and runs process/pipe cleanup | No plan is submitted |
| Injector exits before `READY` | Host raises `RuntimeError` with startup output and runs cleanup | No plan is submitted |
| No `DONE` or `CANCELLED` terminal line | Host raises terminal `TimeoutError` and runs cleanup | Never report `executed` |
| Unknown protocol line with no later valid terminal, or early EOF | Unknown lines are ignored; EOF/process exit without a terminal receipt ends as terminal `TimeoutError` | Treat outcome as unknown/failure, never success |
| Malformed numeric receipt | Host raises `ValueError` while parsing the timestamp and runs cleanup | Never report `executed` |

The transport failure cases above are currently covered by offline fake-process tests.
They do not prove that Android accepts injection or that each failure can be induced on a
physical device. In particular, suppressing a receipt or producing an unknown protocol
line requires a controlled fault-injection harness; do not kill unrelated Android
processes to manufacture the condition. An unknown diagnostic line followed by a valid
`DONE` or `CANCELLED` is resolved by that later terminal receipt, matching current code.

## 1. Build and inspect the real D8 output

Use a machine with JDK 17, Android SDK Build Tools containing `d8`, Python 3, and the
Android SDK platform for API 31 or later. Check tool versions and paths first:

```sh
java -version
javac -version
d8 --version
python3 --version
```

From the repository root, build the injector. This compiles all Java output classes,
passes every generated `.class` file to D8, checks the resulting file, and copies that
same DEX to the runtime asset path:

```sh
bash tools/gesture/build.sh
python3 tools/gesture/check_dex.py tools/gesture/build/classes.dex
sha256sum tools/gesture/build/classes.dex pogobot/vendor/gesture.dex
```

Require the checker to print `DEX contains all required gesture classes` and both SHA-256
values to match. The class definitions that must be present are:

- `Lpogobot/Gesture;`
- `Lpogobot/Gesture$Point;`
- `Lpogobot/Gesture$MotionAction;`

Record the tool versions, command output, DEX SHA-256, and build machine with the test
results. The synthetic DEX unit tests are not a substitute for this real build and
inspection.

## 2. Build and install the harmless touch logger

The small app in `tools/gesture/test_surface` displays and writes every received touch
action to Android logcat. It records action, pointer count and IDs, coordinates,
`downTime`, and `eventTime`. Build it on the Android SDK machine with JDK 17, Gradle 8.7,
Android platform API 35, and SDK Build Tools 34.0.0:

```sh
cd tools/gesture/test_surface
gradle --version
gradle --no-daemon assembleDebug
cd ../../..
```

Confirm the APK exists at
`tools/gesture/test_surface/app/build/outputs/apk/debug/app-debug.apk`. Connect and
unlock the supervised device, enable USB debugging, accept its authorization prompt, and
confirm that its serial appears as `device`, not `unauthorized`:

```sh
adb devices -l
SERIAL='replace-with-the-authorized-device-serial'
adb -s "$SERIAL" shell getprop ro.product.model
adb -s "$SERIAL" shell getprop ro.build.version.release
adb -s "$SERIAL" shell getprop ro.build.version.sdk
adb -s "$SERIAL" shell wm size
adb -s "$SERIAL" shell wm density
adb -s "$SERIAL" install -r tools/gesture/test_surface/app/build/outputs/apk/debug/app-debug.apk
adb -s "$SERIAL" shell am force-stop pogobot.gesturetest
adb -s "$SERIAL" shell am start -n pogobot.gesturetest/.MainActivity
```

Leave `Gesture Touch Log` in the foreground. Do not touch its surface after launch. Clear
old logcat entries, then capture logger output in a second terminal:

```sh
adb -s "$SERIAL" logcat -c
adb -s "$SERIAL" logcat -v brief -s GestureTouchLog:I '*:S'
```

Use the active logical width and height reported by `wm size` for the transport. If an
override size is present, use the override dimensions. Record display rotation too.

## 3. Run a normal plan through the host transport

From the repository root, set the serial and active logical dimensions. The path below is
inside the test surface and has five samples over 400 ms:

```sh
export ANDROID_SERIAL="$SERIAL"
export GESTURE_WIDTH=1080
export GESTURE_HEIGHT=2340
PYTHONPATH=. python3 - <<'PY'
import os
import threading
from pogobot.actions import ADBGestureTransport
from pogobot.gestures import Calibration, plan_straight

width = int(os.environ["GESTURE_WIDTH"])
height = int(os.environ["GESTURE_HEIGHT"])
screen = (width, height)
trajectory = plan_straight((0.25, 0.68), (0.75, 0.32),
                           duration_ms=400, interval_ms=100)
receipt = ADBGestureTransport(screen, serial=os.environ["ANDROID_SERIAL"]).execute(
    trajectory, Calibration(screen), threading.Event(), screen_wh=screen)
print(receipt)
assert receipt.status == "executed"
assert receipt.device_start_ms is not None and receipt.device_end_ms is not None
PY
```

Replace the example dimensions with the device's actual logical dimensions. On the test
surface and in logcat, verify exactly one `DOWN`, ordered `MOVE` events, and one `UP`;
pointer count remains one, pointer ID remains 0, `downTime` is constant, `eventTime`
strictly increases, and coordinates follow the calibrated plan. The host transport
consumes raw injector protocol lines internally; its `GestureReceipt` is the host-visible
receipt. Do not expect `READY`, `START`, or `DONE` to appear in host stdout.

## 4. Cancellation and failure checks

- [ ] **Before submission:** the offline pre-cancel test verifies `execute()` returns
      `cancelled` without starting the injector or writing a plan. A device run is not
      needed to prove this early host guard.
- [ ] **During a gesture:** with the logger foregrounded and untouched, run the included
      watcher. It starts a long plan on a worker and sets the shared cancellation event
      when logcat observes this run's first one-pointer `MOVE`; it does not guess from a
      fixed delay. From the repository root, with the dimensions already set above, run:

      ```sh
      PYTHONPATH=. python3 tools/gesture/device_cancel_smoke.py \
        --serial "$SERIAL" --screen "$GESTURE_WIDTH" "$GESTURE_HEIGHT"
      ```

      Require a host receipt with status `cancelled`. In the logger output, require one
      `CANCEL` after `DOWN`/`MOVE`, with no later `MOVE` or `UP`; verify the worker and
      injector exit and no pointer remains active.
- [ ] **Rejected input:** offline tests feed the host an injector `ERROR` and verify it
      raises `RuntimeError` and cleans up. If the supervised Android platform naturally
      rejects an event, preserve the `ERROR` output and verify no success receipt or stuck
      pointer. Do not try to bypass Android security to force acceptance/rejection.
- [ ] **Startup timeout:** offline tests omit `READY` and verify `TimeoutError` and
      process/pipe cleanup. A device timeout run is optional and must use a controlled
      test harness that withholds startup; never disturb unrelated device services.
- [ ] **Terminal timeout:** offline tests omit a terminal receipt and verify
      `TimeoutError` and cleanup. Do not claim a physical-device timeout was tested unless
      a controlled harness induced it.
- [ ] **Unknown/malformed result:** offline tests verify an unrecognized terminal line
      cannot return `executed`; it times out. Malformed timestamps raise `ValueError`.
      Early EOF without `DONE`/`CANCELLED` is also failure, never success. Any device-side
      protocol fault injection must be isolated and recorded separately.
- [ ] After success, cancellation, or a controlled failure, verify the worker exits,
      pipes close, and the touch surface has no active pointer. Run one new short plan to
      verify the next session starts cleanly.

## 5. Timing, evidence, and limits

- [ ] Agree on coordinate and timing tolerances before testing. Run at least five normal
      plans with the same path and parameters.
- [ ] Record requested duration, logger `downTime`/`eventTime` sequence, observed pixel
      coordinates, host submission-to-start delay, device contact duration, maximum
      inter-event lateness, and any rejected or missing event. Keep host delay separate
      from device contact duration.
- [ ] Record device model, Android build/API, serial, display size/density/rotation,
      injector DEX hash, tool versions, test-surface APK hash, logs, captures, operator,
      date/time, pass/fail for each check, and any device state changes.
- [ ] Any missing/extra event, pointer discontinuity, stuck pointer, unexpected app
      interaction, cleanup failure, malformed/unknown result reported as success, or
      out-of-tolerance timing is a failure.

Passing this procedure demonstrates only the observed injector behavior on the recorded
device and test surface. It does not prove Pokémon GO interaction, gameplay behavior,
throw outcomes, or accuracy. Keep gameplay wiring disabled until the Technical Lead
accepts the shared action/cancellation and identity (`FrameRef`) contracts and the
integration gate.
