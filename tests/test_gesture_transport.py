from queue import Queue
import threading
import time

from pogobot import actions
from pogobot.gestures import Calibration, plan_straight


class FakeInput:
    def __init__(self, process):
        self.process = process
        self.writes = []
    def write(self, line):
        self.writes.append(line)
        if line.startswith("PLAN "):
            self.process.stdout.lines.put("START 1000\n")
            self.process.stdout.lines.put("DONE 1120\n")
        elif line == "CANCEL\n":
            self.process.stdout.lines.put("CANCELLED 1040\n")
    def flush(self):
        return None
    def close(self):
        self.process.stdout.lines.put(None)


class FakeOutput:
    def __init__(self):
        self.lines = Queue()
    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line
    def close(self):
        self.lines.put(None)


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.stdout = FakeOutput()
        self.stdin = FakeInput(self)
        self.returncode = None
        self.stdout.lines.put("READY\n")
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.returncode = 0
        return 0
    def terminate(self):
        self.returncode = -15
    def kill(self):
        self.returncode = -9


def build_plan():
    return plan_straight((.1, .8), (.9, .2), duration_ms=120,
                         interval_ms=60), Calibration((100, 200))


def test_adb_transport_sends_one_plan_and_returns_device_receipt(monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(actions, "_GESTURE_LOCAL", __import__("pathlib").Path("built.dex"))
    monkeypatch.setattr(actions.Path, "is_file", lambda self: True)
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **kw: type(
        "Push", (), {"returncode": 0, "stderr": b""})())
    monkeypatch.setattr(actions.subprocess, "Popen", lambda *a, **kw: process)
    trajectory, calibration = build_plan()
    transport = actions.ADBGestureTransport((100, 200))
    receipt = transport.execute(trajectory, calibration, threading.Event(),
                                submitted_at=time.perf_counter() - .05)
    assert receipt.status == "executed"
    assert receipt.device_start_ms == 1000
    assert receipt.device_end_ms == 1120
    assert receipt.contact_ms == 120
    assert receipt.host_delay_ms >= 50
    assert process.stdin.writes[0].startswith("PLAN 0,10,159;")


def test_adb_transport_propagates_cancel_to_android_process(monkeypatch):
    process = FakeProcess()
    # Do not enqueue DONE for this test. Start the pointer, then let the caller cancel it.
    def write(line):
        process.stdin.writes.append(line)
        if line.startswith("PLAN "):
            process.stdout.lines.put("START 1000\n")
            threading.Timer(.05, cancel.set).start()
        elif line == "CANCEL\n":
            process.stdout.lines.put("CANCELLED 1040\n")
    process.stdin.write = write
    monkeypatch.setattr(actions, "_GESTURE_LOCAL", __import__("pathlib").Path("built.dex"))
    monkeypatch.setattr(actions.Path, "is_file", lambda self: True)
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **kw: type(
        "Push", (), {"returncode": 0, "stderr": b""})())
    monkeypatch.setattr(actions.subprocess, "Popen", lambda *a, **kw: process)
    trajectory, calibration = build_plan()
    transport = actions.ADBGestureTransport((100, 200))
    cancel = threading.Event()
    receipt = transport.execute(trajectory, calibration, cancel)
    assert receipt.status == "cancelled"
    assert process.stdin.writes[-1] == "CANCEL\n"
