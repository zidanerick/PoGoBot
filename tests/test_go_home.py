"""Returning to a fixed saved location after an account switch.

Over a long run the bot walks, and it drifts off the dense area it was started in. A
confirmed switch is the moment to put it back: the switch has already interrupted play, and
the incoming account inherits wherever the outgoing one wandered to.

Everything here is driven from trees captured off the live PGSharp overlay, so the ladder is
tested without a phone. The captures pin two facts the design rests on, and the second one
is the dangerous one:

  * Favourite rows carry their own NAME (`hl_fi_name`) and their own cooldown, so a
    destination can be configured by name instead of by an index into a list that reorders.
  * BACK does NOT dismiss the Favorites page. It is fully present afterwards. Since
    `Recovering`'s ladder presses BACK, a page left open here is a wedge nothing in the bot
    can clear - so the page must be closed by its own control, and the test below asserts
    the capture rather than trusting the recollection.
"""
from pathlib import Path

import pytest

from pogobot import accounts
from pogobot.accounts import AccountView, FavoriteRow, parse_dump, teleport_home
from pogobot.effects import Tap

FIX = Path("tests/fixtures/uiautomator")
WH = (1080, 2340)


def _view(name):
    return parse_dump((FIX / f"{name}.xml").read_bytes(), WH)


class _Reader:
    """Replays a scripted sequence of trees, the way the device would answer."""

    def __init__(self, *names):
        self.queue = list(names)
        self.reads = 0

    def read(self):
        self.reads += 1
        name = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return _view(name) if isinstance(name, str) else name


class _AsyncReader:
    """A deterministic request/poll reader for the navigator's state transitions."""

    def __init__(self, *views):
        self.views = list(views)
        self.requested = False
        self.invalidations = 0

    def invalidate(self):
        self.invalidations += 1
        self.requested = False

    def request(self):
        self.requested = True
        return True

    def poll(self):
        if not self.requested or not self.views:
            return None
        self.requested = False
        return self.views.pop(0)


class _Act:
    def __init__(self):
        self.taps = []

    def apply(self, effect, now=None):
        if isinstance(effect, Tap):
            self.taps.append((round(effect.x, 4), round(effect.y, 4), effect.reason))
        return True

    def healthy(self):
        return True

    def stats(self):
        return {}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(accounts.time, "sleep", lambda *_: None)


# ---------------------------------------------------------------- the captures

def test_the_favourites_page_yields_named_rows_with_cooldowns():
    v = _view("pgsharp_favorites")
    assert v.favorites_open
    assert len(v.favorites) == 7
    first = v.favorites[0]
    assert isinstance(first, FavoriteRow)
    assert first.name and first.tap_norm and first.cooldown_min == 120


def test_back_does_not_dismiss_the_page():
    """The load-bearing capture. If this ever flips, `_close_pgsharp` may stop being
    careful - and until it does, BACK must never be used to leave this page."""
    assert _view("pgsharp_favorites_after_back").favorites_open is True


def test_the_shortcut_menu_offers_favorites():
    assert _view("pgsharp_shortcut_menu").favorites_menu_norm is not None


def test_a_collapsed_overlay_offers_neither():
    v = _view("overlay_collapsed")
    assert v.favorites_menu_norm is None
    assert v.favorites_open is False
    assert v.favorites == ()


# ---------------------------------------------------------------- the ladder

def test_it_opens_the_menu_then_the_page_then_taps_the_named_row():
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    got = teleport_home(r, act, "Home Location")
    assert got == "Home Location"
    reasons = [t[2] for t in act.taps]
    assert any("Favorites" in x for x in reasons)
    assert any("teleport to Home Location" in x for x in reasons)


def test_it_closes_the_page_with_its_own_control_and_never_back():
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    teleport_home(r, act, "Home Location")
    assert any("close the PGSharp page" in t[2] for t in act.taps)
    close = _view("pgsharp_favorites").close_norm
    assert any((t[0], t[1]) == (round(close[0], 4), round(close[1], 4)) for t in act.taps)


def test_it_closes_the_shortcut_menu_too():
    """A menu left open sits over the reach ellipse and the bot targets through it for the
    rest of the run - see Switching._autowalk_close."""
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    teleport_home(r, act, "Home Location")
    assert any("close the PGSharp shortcut menu" in t[2] for t in act.taps)


def test_a_name_that_is_not_there_taps_no_row():
    """Whether PGSharp keeps favourites per account or per install is UNMEASURED. If they
    are per-account, an account without the entry must do nothing at all rather than tap
    whatever occupies those pixels."""
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    assert teleport_home(r, act, "Atlantis") is None
    assert not any("teleport to" in t[2] for t in act.taps)
    assert any("close the PGSharp page" in t[2] for t in act.taps), "and still closes up"


def test_a_partial_name_matches_the_real_row():
    """The real rows carry a flag emoji and a country nobody will type."""
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    assert teleport_home(r, act, "saved location 3") == "Saved Location 3"


def test_an_exact_name_beats_a_longer_row_that_contains_it():
    v = _view("pgsharp_favorites")
    names = [r.name for r in v.favorites]
    assert "Saved Location 1" in names
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    assert teleport_home(r, act, "Saved Location 1") == "Saved Location 1"


def test_an_unreadable_overlay_taps_nothing():
    class _Dead:
        def read(self):
            return parse_dump(b"<hierarchy/>", WH)

    act = _Act()
    assert teleport_home(_Dead(), act, "Home Location") is None
    assert act.taps == []


def test_a_page_already_open_is_used_where_it_stands():
    """What a previous attempt that could not close leaves behind. Tapping the star here
    would toggle something else, not re-open this."""
    act = _Act()
    r = _Reader("pgsharp_favorites")
    assert teleport_home(r, act, "Home Location") == "Home Location"
    assert not any("shortcut menu" in t[2] and "open" in t[2] for t in act.taps)


def test_every_tap_is_booked_to_its_own_budget():
    """So a go-home that misbehaves cannot spend the switch or identify budget."""
    act = _Act()
    r = _Reader("pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites")
    teleport_home(r, act, "Home Location")
    assert act.taps


# ---------------------------------------------------------------- configuration

def test_it_is_off_unless_a_name_is_configured():
    from pogobot.config import DEFAULT
    assert DEFAULT.home_favorite == ""


def test_an_account_can_name_its_own_home():
    from pogobot import userconfig
    prof = userconfig.load_profiles({"accounts": {"A": {"home_favorite": "New York"}}})
    assert prof["A"]["home_favorite"] == "New York"


def test_a_non_string_home_is_refused(caplog):
    from pogobot import userconfig
    with caplog.at_level("WARNING"):
        prof = userconfig.load_profiles({"accounts": {"A": {"home_favorite": 5}}})
    assert prof["A"] == {}
    assert "expected the name" in caplog.text


# ---------------------------------------------------------------- the runner's half

def _runner(**kw):
    from pogobot import runner as runner_mod
    from pogobot.config import DEFAULT

    class _Src:
        def read(self):
            return None

        def healthy(self):
            return True

        def release(self):
            pass

    class _A:
        def apply(self, e, now=None):
            return True

        def healthy(self):
            return True

        def stats(self):
            return {}

        def close(self):
            pass

    return runner_mod.Runner(kw.pop("cfg", DEFAULT), _Src(), _A(), perceptor=None,
                             display=False, **kw)


def test_a_run_with_no_home_configured_does_nothing():
    r = _runner(tree_reader=_Reader("pgsharp_shortcut_menu"))
    r._go_home()
    assert _Reader("pgsharp_shortcut_menu").reads == 0


def test_a_run_with_no_tree_reader_does_nothing():
    from dataclasses import replace
    from pogobot.config import DEFAULT
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"))
    r.tree_reader = None
    r._go_home()


def test_the_incoming_accounts_own_home_is_used():
    """`_apply_account_profile` has not run for the incoming account yet - it is driven from
    the tick loop - so reading `self.cfg` would use the OUTGOING account's home."""
    from pogobot import userconfig
    prof = userconfig.load_profiles(
        {"accounts": {"Traveller": {"home_favorite": "Saved Location 3"}}})
    r = _runner(account_profiles=prof,
                tree_reader=_Reader("pgsharp_shortcut_menu", "pgsharp_favorites",
                                    "pgsharp_favorites"))
    r.stats.account = "Traveller"
    r._go_home()


def test_a_failed_go_home_is_reported_but_does_not_raise():
    from dataclasses import replace
    from pogobot.config import DEFAULT

    class _Rec:
        enabled = False

        def __init__(self):
            self.problems = []

        def problem(self, title, detail=""):
            self.problems.append(title)

        def started(self, **k):
            pass

        def finished(self, **k):
            pass

        def halted(self, *a, **k):
            pass

        def switched(self, n):
            pass

        def close(self, timeout=5.0):
            pass

    rec = _Rec()
    r = _runner(cfg=replace(DEFAULT, home_favorite="Atlantis"), notifier=rec,
                tree_reader=_Reader("pgsharp_shortcut_menu", "pgsharp_favorites",
                                    "pgsharp_favorites"))
    r._go_home()
    assert rec.problems == ["Could not return home"]


def test_account_switch_with_async_reader_returns_to_configured_home():
    """The production CLI wraps this reader, so a confirmed switch must not call read()."""
    from dataclasses import replace
    from time import monotonic, sleep
    from pogobot.accounts import AsyncUiTreeReader
    from pogobot.config import DEFAULT

    class _Rec:
        enabled = False

        def __init__(self):
            self.problems = []

        def problem(self, title, detail=""):
            self.problems.append((title, detail))

        def started(self, **k): pass
        def finished(self, **k): pass
        def halted(self, *a, **k): pass
        def switched(self, n): pass
        def close(self, timeout=5.0): pass

    rec = _Rec()
    reader = AsyncUiTreeReader(_Reader(
        "pgsharp_shortcut_menu", "pgsharp_favorites", "pgsharp_favorites",
        "pgsharp_shortcut_menu", AccountView(available=True)))
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"),
                tree_reader=reader, notifier=rec)
    r.actuator = _Act()
    started = monotonic()
    r._on_switch_confirmed("Traveller")
    assert monotonic() - started < 0.1
    assert r._home_navigation is not None
    # Skip UI settling in this deterministic fixture; production uses the 1.2s deadline.
    r._home_navigation.settle = 0

    deadline = monotonic() + 2
    while r._home_navigation is not None and monotonic() < deadline:
        r._poll_home_navigation()
        sleep(0.002)

    assert r._home_navigation is None
    assert any(reason == "home: teleport to Home Location"
               for _, _, reason in r.actuator.taps)
    assert rec.problems == []
    reader.close()


def test_async_home_navigation_failure_reaches_notifier():
    from dataclasses import replace
    from time import monotonic, sleep
    from pogobot.accounts import AccountView, AsyncUiTreeReader
    from pogobot.config import DEFAULT

    class _Rec:
        enabled = False

        def __init__(self):
            self.problems = []

        def problem(self, title, detail=""):
            self.problems.append((title, detail))

        def started(self, **k): pass
        def finished(self, **k): pass
        def halted(self, *a, **k): pass
        def switched(self, n): pass
        def close(self, timeout=5.0): pass

    rec = _Rec()
    reader = AsyncUiTreeReader(accounts.FakeTreeReader(
        [AccountView(available=False)]))
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"),
                tree_reader=reader, notifier=rec)
    r._on_switch_confirmed("Traveller")
    deadline = monotonic() + 2
    while r._home_navigation is not None and monotonic() < deadline:
        r._poll_home_navigation()
        sleep(0.002)

    assert r._home_navigation is None
    assert len(rec.problems) == 1
    assert rec.problems[0][0] == "Could not return home"
    assert "Could not read" in rec.problems[0][1]
    reader.close()


def test_runner_reports_unknown_after_favorite_tap_without_retry_or_success(caplog):
    """Runner polling reports an unavailable post-teleport result as failure only."""
    from dataclasses import replace
    import logging
    from time import monotonic, sleep
    from pogobot.accounts import AccountView, AsyncUiTreeReader
    from pogobot.config import DEFAULT

    class _ScriptedReader:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return _view("pgsharp_favorites")
            return AccountView(available=False)

    class _Recorder:
        enabled = False

        def __init__(self):
            self.events = []

        def problem(self, title, detail=""):
            self.events.append(("problem", title, detail))

        def switched(self, name):
            self.events.append(("switched", name))

        def finished(self, **kwargs):
            self.events.append(("finished", kwargs))

        def started(self, **kwargs): pass
        def halted(self, *args, **kwargs): pass
        def close(self, timeout=5.0): pass

    source = _ScriptedReader()
    reader = AsyncUiTreeReader(source)
    recorder = _Recorder()
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"),
                tree_reader=reader, notifier=recorder)
    r.actuator = _Act()
    caplog.set_level(logging.INFO)

    r._on_switch_confirmed("Traveller")
    navigator = r._home_navigation
    assert navigator is not None
    navigator.settle = 0
    deadline = monotonic() + 2
    while r._home_navigation is not None and monotonic() < deadline:
        r._poll_home_navigation()
        sleep(0.002)
    # A later tick must not resend the notifier after the navigation is retired.
    r._poll_home_navigation()

    assert r._home_navigation is None
    assert navigator.result is None
    assert navigator.failure == "Could not verify the saved-location tap"
    assert [event[0] for event in recorder.events] == ["switched", "problem"]
    failures = [event for event in recorder.events if event[0] == "problem"]
    assert len(failures) == 1
    assert failures[0][1] == "Could not return home"
    assert "Could not verify the saved-location tap" in failures[0][2]
    assert not any("back at " in record.getMessage() for record in caplog.records)
    assert source.reads == 2, "one initial read and one post-tap read, with no retry"
    assert [reason for _, _, reason in r.actuator.taps] == [
        "home: teleport to Home Location"]
    reader.close()


def test_rejected_favorite_tap_cannot_report_success():
    from pogobot.accounts import AsyncHomeNavigator

    class _Reject:
        def __init__(self):
            self.taps = []

        def apply(self, effect, now=None):
            self.taps.append(effect)
            return False

    act = _Reject()
    nav = AsyncHomeNavigator(_AsyncReader(_view("pgsharp_favorites")), act,
                             "Home Location", settle=0)
    assert nav.advance() is False
    assert nav.result is None
    assert nav.failure and "Could not dispatch home: teleport" in nav.failure
    assert len(act.taps) == 1


def test_unavailable_tree_after_favorite_tap_is_not_success():
    from pogobot.accounts import AccountView, AsyncHomeNavigator

    act = _Act()
    nav = AsyncHomeNavigator(
        _AsyncReader(_view("pgsharp_favorites"), AccountView(available=False)),
        act, "Home Location", settle=0)
    assert nav.advance() is None  # Accepted row tap, no navigation result yet.
    assert nav.advance() is False
    assert nav.result is None
    assert nav.failure == "Could not verify the saved-location tap"


def test_unavailable_tree_after_page_close_is_not_success():
    from pogobot.accounts import AccountView, AsyncHomeNavigator

    act = _Act()
    nav = AsyncHomeNavigator(
        _AsyncReader(_view("pgsharp_favorites"), _view("pgsharp_favorites"),
                     AccountView(available=False)),
        act, "Home Location", settle=0)
    assert nav.advance() is None  # Favorite tapped.
    assert nav.advance() is None  # Page close tapped.
    assert nav.advance() is False
    assert nav.result is None
    assert nav.failure == "Could not verify the PGSharp page closed"


def test_missing_favorite_cleans_up_page_and_menu_then_reports_failure():
    from pogobot.accounts import AccountView, AsyncHomeNavigator

    act = _Act()
    nav = AsyncHomeNavigator(
        _AsyncReader(_view("pgsharp_favorites"), _view("pgsharp_shortcut_menu"),
                     AccountView(available=True)),
        act, "Atlantis", settle=0)
    assert nav.advance() is None  # Missing row; close the Favorites page.
    assert nav.advance() is None  # Close the shortcut menu.
    assert nav.advance() is False
    assert nav.result is None
    reasons = [reason for _, _, reason in act.taps]
    assert reasons == ["home: close the PGSharp page",
                       "home: close the PGSharp shortcut menu"]


def test_async_home_navigation_timeout_reports_failure(monkeypatch):
    from pogobot.accounts import AsyncHomeNavigator

    clock = [50.0]
    monkeypatch.setattr(accounts.time, "monotonic", lambda: clock[0])
    nav = AsyncHomeNavigator(_AsyncReader(), _Act(), "Home Location", settle=0)
    assert nav.advance() is None
    clock[0] += nav.TOTAL_TIMEOUT
    assert nav.advance() is False
    assert nav.result is None
    assert nav.failure == "Timed out waiting for the PGSharp overlay"


def test_async_home_navigation_discards_stale_snapshot_after_favorite_tap():
    from threading import Event
    from time import monotonic, sleep
    from pogobot.accounts import AccountView, AsyncHomeNavigator, AsyncUiTreeReader

    entered, release = Event(), Event()

    class _SlowThenUnavailable:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return _view("pgsharp_favorites")
            if self.reads == 2:
                entered.set()
                release.wait(1)
                return _view("pgsharp_favorites")
            return AccountView(available=False)

    underlying = _SlowThenUnavailable()
    reader = AsyncUiTreeReader(underlying)
    act = _Act()
    nav = AsyncHomeNavigator(reader, act, "Home Location", settle=0)
    deadline = monotonic() + 1
    while reader._running and monotonic() < deadline:
        sleep(0.002)
    assert reader.request(), "start a second, deliberately stale snapshot"
    assert entered.wait(0.5)
    assert nav.advance() is None  # Consume the first view and tap the favorite.
    assert [reason for _, _, reason in act.taps] == [
        "home: teleport to Home Location"]
    release.set()

    deadline = monotonic() + 1
    while reader._running and monotonic() < deadline:
        sleep(0.002)
    assert reader.poll() is None, "the in-flight pre-tap snapshot must be discarded"
    deadline = monotonic() + 1
    while not nav.finished and monotonic() < deadline:
        nav.advance()
        sleep(0.002)
    assert nav.advance() is False
    assert nav.result is None
    assert [reason for _, _, reason in act.taps] == [
        "home: teleport to Home Location"], "stale state must not authorize cleanup taps"
    reader.close()


def test_pause_invalidates_home_snapshot_and_resume_uses_fresh_tree():
    from dataclasses import replace
    from threading import Event
    from time import monotonic, sleep
    from pogobot.accounts import AccountView, AsyncUiTreeReader
    from pogobot.config import DEFAULT

    entered, release = Event(), Event()

    class _SlowThenUnavailable:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                entered.set()
                release.wait(1)
                return _view("pgsharp_favorites")
            return AccountView(available=False)

    class _Rec:
        enabled = False

        def __init__(self): self.problems = []
        def problem(self, title, detail=""): self.problems.append((title, detail))
        def started(self, **k): pass
        def finished(self, **k): pass
        def halted(self, *a, **k): pass
        def switched(self, n): pass
        def close(self, timeout=5.0): pass

    reader = AsyncUiTreeReader(_SlowThenUnavailable())
    rec = _Rec()
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"),
                tree_reader=reader, notifier=rec)
    r.actuator = _Act()
    r._on_switch_confirmed("Traveller")
    assert entered.wait(0.5)
    r.toggle_pause()
    assert r._sync_pause(100.0)
    release.set()
    deadline = monotonic() + 1
    while reader._running and monotonic() < deadline:
        sleep(0.002)
    assert reader.poll() is None
    assert r.actuator.taps == []

    r.toggle_pause()
    assert not r._sync_pause(101.0)
    deadline = monotonic() + 1
    while r._home_navigation is not None and monotonic() < deadline:
        r._poll_home_navigation()
        sleep(0.002)
    assert r._home_navigation is None
    assert r.actuator.taps == []
    assert len(rec.problems) == 1
    assert "Could not read" in rec.problems[0][1]
    reader.close()


def test_runner_close_cancels_pending_home_navigation_without_input():
    from dataclasses import replace
    from threading import Event
    from pogobot.accounts import AsyncUiTreeReader
    from pogobot.config import DEFAULT

    entered, release = Event(), Event()

    class _SlowReader:
        def read(self):
            entered.set()
            release.wait(1)
            return _view("pgsharp_favorites")

    class _Rec:
        enabled = False

        def __init__(self): self.problems = []
        def problem(self, title, detail=""): self.problems.append((title, detail))
        def started(self, **k): pass
        def finished(self, **k): pass
        def halted(self, *a, **k): pass
        def switched(self, n): pass
        def close(self, timeout=5.0): pass

    reader = AsyncUiTreeReader(_SlowReader())
    rec = _Rec()
    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"),
                tree_reader=reader, notifier=rec)
    r.actuator = _Act()
    r._on_switch_confirmed("Traveller")
    nav = r._home_navigation
    assert entered.wait(0.5)
    r.close()
    release.set()
    r._poll_home_navigation()
    assert nav.cancelled
    assert r.actuator.taps == []
    assert rec.problems == []


def test_a_ladder_that_throws_cannot_break_the_switch():
    from dataclasses import replace
    from pogobot.config import DEFAULT

    class _Boom:
        def read(self):
            raise RuntimeError("adb died")

    r = _runner(cfg=replace(DEFAULT, home_favorite="Home Location"), tree_reader=_Boom())
    r._go_home()
