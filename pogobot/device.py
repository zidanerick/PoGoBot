"""Device facts and slow device queries, kept off the hot loop.

v1 called `adb shell dumpsys input_method` synchronously inside the main loop. Measured on
this machine it takes 0.08-0.11s, so every check stalled the loop for ~10 frames. Worse,
v1's 2-second throttle only advanced when the keyboard was actually found, so in the
common case (no keyboard) the blocking call ran on EVERY iteration. Here it runs on a
background thread and the loop reads a cached Tristate.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Optional

from .contracts import Clock, DeviceSnapshot, MonotonicSeconds
from .observation import Tristate

DEFAULT_WH = (1080, 2340)


def screen_size(adb: str = "adb", serial: Optional[str] = None) -> tuple[int, int]:
    """Logical display size. Override wins: `adb input tap` uses the override space."""
    cmd = [adb] + (["-s", serial] if serial else []) + ["shell", "wm", "size"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=5).stdout.decode()
    except Exception:
        return DEFAULT_WH
    for pattern in (r"Override size:\s*(\d+)x(\d+)", r"Physical size:\s*(\d+)x(\d+)"):
        m = re.search(pattern, out)
        if m:
            return int(m.group(1)), int(m.group(2))
    return DEFAULT_WH


def device_online(adb: str = "adb", serial: Optional[str] = None) -> bool:
    cmd = [adb] + (["-s", serial] if serial else []) + ["get-state"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        return r.returncode == 0 and b"device" in r.stdout
    except Exception:
        return False


def app_running(package: str, adb: str = "adb", serial: Optional[str] = None) -> bool:
    """Whether the game's process exists at all. `pidof` is one cheap shell call."""
    cmd = [adb] + (["-s", serial] if serial else []) + ["shell", "pidof", package]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def app_foreground(package: str, adb: str = "adb", serial: Optional[str] = None) -> bool:
    cmd = ([adb] + (["-s", serial] if serial else [])
           + ["shell", "dumpsys", "window"])
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=8).stdout.decode(errors="replace")
    except Exception:
        return False
    for line in out.splitlines():
        if "mCurrentFocus" in line:
            return package in line
    return False


def ensure_app_running(package: str, activity: str, adb: str = "adb",
                       serial: Optional[str] = None, timeout: float = 40.0,
                       log=None) -> bool:
    """Start the game if it is not already up, and wait for it to reach the foreground.

    The capture layer needs this, not merely the bot's own logic. scrcpy records the
    display through a hardware encoder, and an encoder emits frames when the display
    CHANGES: on a launcher sitting perfectly still, nothing is produced at all, and
    `ScrcpySource` raises `CaptureError: no video ... within 12.0s` before the FSM ever
    runs. Reproduced exactly that way - `am force-stop`, start the bot, crash - and the
    same start succeeds the moment the game is on screen, because the game animates
    constantly.

    Returns whether the game is in the foreground when this gives up waiting. False is not
    fatal on its own: the caller is better placed to decide, and a game that is slow to the
    foreground may still get there before BOOT's own budget runs out.
    """
    if app_foreground(package, adb, serial):
        return True
    started = app_running(package, adb, serial)
    if log is not None:
        log.info("the game is %s; starting it before capture opens",
                 "running but not in front" if started else "not running")
    cmd = ([adb] + (["-s", serial] if serial else [])
           + ["shell", "am", "start", "-n", f"{package}/{activity}"])
    try:
        subprocess.run(cmd, capture_output=True, timeout=15)
    except Exception:
        if log is not None:
            log.warning("could not start %s", package)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if app_foreground(package, adb, serial):
            if log is not None:
                log.info("the game is up")
            return True
        time.sleep(1.0)
    if log is not None:
        log.warning("%s did not reach the foreground within %.0fs; opening capture anyway",
                    package, timeout)
    return False


class KeyboardPoller:
    """Publishes a Tristate the loop can read for free.

    UNKNOWN is a real answer: v1 swallowed every failure into False, so an adb error read
    as a confident 'no keyboard is up'.
    """

    def __init__(self, adb: str = "adb", serial: Optional[str] = None, interval: float = 2.0):
        self._cmd = [adb] + (["-s", serial] if serial else []) + ["shell", "dumpsys", "input_method"]
        self._interval = interval
        self._state = Tristate.UNKNOWN
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="keyboard-poller", daemon=True)

    def start(self) -> "KeyboardPoller":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(self._cmd, capture_output=True, timeout=4).stdout.decode()
                value = Tristate.TRUE if "mInputShown=true" in out else Tristate.FALSE
            except Exception:
                value = Tristate.UNKNOWN
            with self._lock:
                self._state = value
            self._stop.wait(self._interval)

    @property
    def state(self) -> Tristate:
        with self._lock:
            return self._state

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


class FakeDeviceServices:
    """Deterministic device facts for tests and explicit offline composition."""

    def __init__(self, initial: Optional[DeviceSnapshot] = None):
        self._snapshot = initial
        self.calls = 0
        self.closed = False

    def snapshot(self, now_mono_s: MonotonicSeconds) -> DeviceSnapshot:
        self.calls += 1
        if self._snapshot is not None:
            return self._snapshot
        return DeviceSnapshot(now_mono_s, Tristate.UNKNOWN, Tristate.UNKNOWN,
                              Tristate.UNKNOWN, None)

    def set_snapshot(self, snapshot: DeviceSnapshot) -> None:
        self._snapshot = snapshot

    def close(self) -> None:
        self.closed = True


class ReplayDeviceServices(FakeDeviceServices):
    """Replay facts are unknown by default and never query the host or an ADB device."""

    @property
    def external_calls(self) -> int:
        return 0


class LiveDeviceServices:
    """Nonblocking cached device snapshot populated by one bounded worker thread."""

    def __init__(self, clock: Clock, package: str, adb: str = "adb",
                 serial: Optional[str] = None, interval: float = 1.0,
                 max_age: float = 10.0, autostart: bool = True):
        self.clock = clock
        self.package = package
        self.adb = adb
        self.serial = serial
        self.interval = max(0.1, float(interval))
        self.max_age = max(0.1, float(max_age))
        self._cv = threading.Condition()
        self._stop = threading.Event()
        now = clock.monotonic_now()
        self._snapshot = DeviceSnapshot(now, Tristate.UNKNOWN, Tristate.UNKNOWN,
                                        Tristate.UNKNOWN, None)
        self._revision = 0
        self._thread = threading.Thread(target=self._poll, name="device-services", daemon=True)
        if autostart:
            self.start()

    def start(self) -> None:
        if not self._thread.is_alive() and not self._stop.is_set():
            self._thread.start()

    def _argv(self, *parts: str) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else []) + list(parts)

    def _query(self, *parts: str, timeout: float) -> Optional[str]:
        try:
            result = subprocess.run(self._argv(*parts), capture_output=True,
                                    timeout=timeout)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode(errors="replace")

    def _sample(self) -> DeviceSnapshot:
        state = self._query("get-state", timeout=3.0)
        online = (Tristate.UNKNOWN if state is None else
                  Tristate.TRUE if state.strip() == "device" else Tristate.FALSE)

        windows = self._query("shell", "dumpsys", "window", timeout=5.0)
        foreground = Tristate.UNKNOWN
        if windows is not None:
            focus = next((line for line in windows.splitlines()
                          if "mCurrentFocus" in line), None)
            if focus is not None:
                foreground = Tristate.TRUE if self.package in focus else Tristate.FALSE

        size_text = self._query("shell", "wm", "size", timeout=3.0)
        logical_wh = None
        if size_text is not None:
            for pattern in (r"Override size:\s*(\d+)x(\d+)",
                            r"Physical size:\s*(\d+)x(\d+)"):
                match = re.search(pattern, size_text)
                if match:
                    logical_wh = (int(match.group(1)), int(match.group(2)))
                    break

        keyboard_text = self._query("shell", "dumpsys", "input_method", timeout=4.0)
        keyboard = Tristate.UNKNOWN
        if keyboard_text is not None:
            if "mInputShown=true" in keyboard_text:
                keyboard = Tristate.TRUE
            elif "mInputShown=false" in keyboard_text:
                keyboard = Tristate.FALSE

        return DeviceSnapshot(self.clock.monotonic_now(), foreground, keyboard,
                              online, logical_wh)

    def _poll(self) -> None:
        while not self._stop.is_set():
            snapshot = self._sample()
            self.publish(snapshot)
            self._stop.wait(self.interval)

    def publish(self, snapshot: DeviceSnapshot) -> None:
        """Atomically publish a completed worker sample; also the fake-clock test seam."""
        with self._cv:
            self._snapshot = snapshot
            self._revision += 1
            self._cv.notify_all()

    def startup_snapshot(self, timeout: float = 12.0) -> DeviceSnapshot:
        """Wait only during CLI startup for one cache fill, never from Runner's loop."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cv:
            while self._revision == 0 and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(remaining)
            return self._snapshot

    def snapshot(self, now_mono_s: MonotonicSeconds) -> DeviceSnapshot:
        with self._cv:
            snapshot = self._snapshot
        if float(now_mono_s) - float(snapshot.sampled_mono_s) <= self.max_age:
            return snapshot
        return DeviceSnapshot(snapshot.sampled_mono_s, Tristate.UNKNOWN,
                              Tristate.UNKNOWN, Tristate.UNKNOWN, None)

    def close(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
