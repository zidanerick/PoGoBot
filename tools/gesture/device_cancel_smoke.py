#!/usr/bin/env python3
"""Cancel one injector run after the harmless logger observes its first MOVE."""
from __future__ import annotations

import argparse
import subprocess
import threading
import time

from pogobot.actions import ADBGestureTransport
from pogobot.gestures import Calibration, plan_straight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--screen", nargs=2, type=int, required=True,
                        metavar=("WIDTH", "HEIGHT"))
    args = parser.parse_args()
    width, height = args.screen
    screen = (width, height)

    # Clearing first and not touching the logger surface makes the first matching MOVE
    # attributable to this test run. It is only a trigger, not a device-side verdict.
    subprocess.run(["adb", "-s", args.serial, "logcat", "-c"],
                   check=True, timeout=5)
    logcat = subprocess.Popen(
        ["adb", "-s", args.serial, "logcat", "-v", "brief", "-s",
         "GestureTouchLog:I", "*:S"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    cancel = threading.Event()
    saw_move = threading.Event()
    result: dict[str, object] = {}

    def watch_surface() -> None:
        assert logcat.stdout is not None
        for line in logcat.stdout:
            print(line, end="")
            if " MOVE pointers=1 " in line:
                saw_move.set()
                cancel.set()
                return

    def execute() -> None:
        try:
            trajectory = plan_straight((0.25, 0.68), (0.75, 0.32),
                                       duration_ms=5000, interval_ms=100)
            result["receipt"] = ADBGestureTransport(
                screen, serial=args.serial).execute(
                    trajectory, Calibration(screen), cancel, screen_wh=screen)
        except BaseException as exc:
            result["error"] = exc

    watcher = threading.Thread(target=watch_surface, name="gesture-touch-log")
    worker = threading.Thread(target=execute, name="gesture-device-smoke")
    try:
        watcher.start()
        # Allow logcat to attach before the transport submits the gesture.
        time.sleep(0.25)
        worker.start()
        worker.join(timeout=15)
        if worker.is_alive():
            cancel.set()
            worker.join(timeout=2)
            raise TimeoutError("gesture worker did not stop after cancellation")
        if "error" in result:
            raise result["error"]
        if not saw_move.is_set():
            raise RuntimeError("logger did not observe a MOVE; cancellation was not tested")
        receipt = result.get("receipt")
        if getattr(receipt, "status", None) != "cancelled":
            raise RuntimeError(f"expected cancelled host receipt, got {receipt!r}")
        print(f"host receipt: {receipt}")
        return 0
    finally:
        cancel.set()
        if logcat.poll() is None:
            logcat.terminate()
        try:
            logcat.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logcat.kill()
            logcat.wait(timeout=2)
        if worker.is_alive():
            worker.join(timeout=2)
        if watcher.is_alive():
            watcher.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
