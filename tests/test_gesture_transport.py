from queue import Queue
import threading
import time

from pogobot import actions
from pogobot.gestures import Calibration, plan_straight


class FakeInput:
    def __init__(self, process, *, cancel=None, cancel_on_plan=False):
        self.process = process
        self.cancel = cancel
        self.cancel_on_plan = cancel_on_plan
        self.writes = []
        self.closed = False
    def write(self, line):
        self.writes.append(line)
        if line.startswith("PLAN "):
            for response in self.process.plan_responses:
                self.process.stdout.lines.put(response + "\n")
            if self.cancel_on_plan:
                self.cancel.set()
        elif line == "CANCEL\n":
            for response in self.process.cancel_responses:
                self.process.stdout.lines.put(response + "\n")
    def flush(self):
        return None
    def close(self):
        self.closed = True
        self.process.stdout.lines.put(None)


class FakeOutput:
    def __init__(self):
        self.lines = Queue()
        self.closed = False
    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line
    def close(self):
        self.closed = True
        self.lines.put(None)


class FakeProcess:
    def __init__(self, *, ready=True, plan_responses=("START 1000", "DONE 1120"),
                 cancel_responses=("CANCELLED 1040",), exit_code=0,
                 cancel=None, cancel_on_plan=False):
        self.stdout = FakeOutput()
        self.stdin = FakeInput(self, cancel=cancel, cancel_on_plan=cancel_on_plan)
        self.plan_responses = plan_responses
        self.cancel_responses = cancel_responses
        self.exit_code = exit_code
        self.returncode = None
        self.wait_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        if ready:
            self.stdout.lines.put("READY\n")
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = self.exit_code
        return self.exit_code
    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15
    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


def build_plan():
    return plan_straight((.1, .8), (.9, .2), duration_ms=120,
                         interval_ms=60), Calibration((100, 200))


def prepare_transport(monkeypatch, process, *, timeout=actions.ADB_TIMEOUT):
    monkeypatch.setattr(actions, "_GESTURE_LOCAL", __import__("pathlib").Path("built.dex"))
    monkeypatch.setattr(actions.Path, "is_file", lambda self: True)
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **kw: type(
        "Push", (), {"returncode": 0, "stderr": b""})())
    monkeypatch.setattr(actions.subprocess, "Popen", lambda *a, **kw: process)
    return actions.ADBGestureTransport((100, 200), timeout=timeout)


def assert_cleaned_up(process):
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.wait_calls >= 1
    assert process.poll() is not None


def test_adb_transport_sends_one_plan_and_returns_device_receipt(monkeypatch):
    process = FakeProcess()
    transport = prepare_transport(monkeypatch, process)
    trajectory, calibration = build_plan()
    receipt = transport.execute(trajectory, calibration, threading.Event(),
                                submitted_at=time.perf_counter() - .05)
    assert receipt.status == "executed"
    assert receipt.device_start_ms == 1000
    assert receipt.device_end_ms == 1120
    assert receipt.contact_ms == 120
    assert receipt.host_delay_ms >= 50
    assert process.stdin.writes[0].startswith("PLAN 0,10,159;")
    assert_cleaned_up(process)


def test_adb_transport_propagates_cancel_to_android_process(monkeypatch):
    cancel = threading.Event()
    process = FakeProcess(plan_responses=("START 1000",), cancel=cancel,
                          cancel_on_plan=True)
    transport = prepare_transport(monkeypatch, process)
    trajectory, calibration = build_plan()
    receipt = transport.execute(trajectory, calibration, cancel)
    assert receipt.status == "cancelled"
    assert process.stdin.writes[-1] == "CANCEL\n"
    assert_cleaned_up(process)


def test_pre_cancelled_action_does_not_start_injector_or_submit_plan(monkeypatch):
    process = FakeProcess()
    transport = prepare_transport(monkeypatch, process)
    popen_calls = []
    monkeypatch.setattr(actions.subprocess, "Popen",
                        lambda *a, **kw: popen_calls.append((a, kw)) or process)
    trajectory, calibration = build_plan()
    cancel = threading.Event()
    cancel.set()

    receipt = transport.execute(trajectory, calibration, cancel)

    assert receipt.status == "cancelled"
    assert popen_calls == []
    assert process.stdin.writes == []
    assert process.wait_calls == 0


def test_injector_startup_timeout_cleans_up_process(monkeypatch):
    process = FakeProcess(ready=False)
    transport = prepare_transport(monkeypatch, process, timeout=.01)
    trajectory, calibration = build_plan()

    try:
        transport.execute(trajectory, calibration, threading.Event())
    except TimeoutError as exc:
        assert "startup timed out" in str(exc)
    else:
        raise AssertionError("startup without READY must time out")
    assert process.stdin.writes == ["CANCEL\n"]
    assert_cleaned_up(process)


def test_injector_rejection_response_is_reported_and_cleaned_up(monkeypatch):
    process = FakeProcess(plan_responses=("ERROR rejected MotionEvent",))
    transport = prepare_transport(monkeypatch, process)
    trajectory, calibration = build_plan()

    try:
        transport.execute(trajectory, calibration, threading.Event())
    except RuntimeError as exc:
        assert "ERROR rejected MotionEvent" in str(exc)
    else:
        raise AssertionError("injector rejection must be reported")
    assert_cleaned_up(process)


def test_malformed_numeric_receipt_is_rejected_and_cleaned_up(monkeypatch):
    process = FakeProcess(plan_responses=("START not-a-timestamp",))
    transport = prepare_transport(monkeypatch, process)
    trajectory, calibration = build_plan()

    try:
        transport.execute(trajectory, calibration, threading.Event())
    except ValueError:
        pass
    else:
        raise AssertionError("malformed numeric receipt must be rejected")
    assert_cleaned_up(process)


def test_missing_terminal_receipt_times_out_and_cleans_up(monkeypatch):
    process = FakeProcess(plan_responses=("START 1000",))
    transport = prepare_transport(monkeypatch, process, timeout=.01)
    trajectory, calibration = build_plan()
    clock_values = iter((0.0, 0.0, 0.0, 0.0, 3.0))
    monkeypatch.setattr(actions.time, "perf_counter",
                        lambda: next(clock_values, 3.0))

    try:
        transport.execute(trajectory, calibration, threading.Event())
    except TimeoutError as exc:
        assert "terminal receipt" in str(exc)
    else:
        raise AssertionError("missing terminal response must time out")
    assert_cleaned_up(process)


def test_unknown_terminal_response_times_out_without_success(monkeypatch):
    process = FakeProcess(plan_responses=("START 1000", "MAYBE 1120"))
    transport = prepare_transport(monkeypatch, process, timeout=.01)
    trajectory, calibration = build_plan()

    try:
        transport.execute(trajectory, calibration, threading.Event())
    except TimeoutError as exc:
        assert "MAYBE 1120" in str(exc)
        assert "terminal receipt" in str(exc)
    else:
        raise AssertionError("unknown terminal response must never report success")
    assert_cleaned_up(process)
