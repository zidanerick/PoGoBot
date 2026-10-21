# PoGoBot shared interfaces and ordered implementation backlog

Status: proposed integration contract v1.1, 21 October 2026. This revision resolves independent-review gaps in clock domains and capture identity. APIs remain proposals until implemented and accepted. PR #1 is documentation only: every API and field introduced there remains a proposal until its owning implementation ticket is integrated and passes its acceptance tests.

Repository: https://github.com/zidanerick/PoGoBot

Inspected HEAD: `9795280ec52b67cde316c45aa531e64c39ddd9b8`. This follows the upstream audit baseline `78149d3ae124e84c604e99ac96ef91a193111de2`. Read `docs/AUTOPLAY_PROJECT_PLAN.md` for the audit, evidence limitations and role prompts. Recheck HEAD before implementation and preserve newer changes.

## Architecture decision and boundaries

Keep capture -> Observation -> pure FSM -> Effects -> runner -> serialized actuator. Workers publish data; only the runner mutates Context, statistics and ledgers. No LLM enters the real-time loop. Add adapters and focused contracts before extracting large modules.

Source inspected for this plan: `frames.py`, `observation.py`, `effects.py`, `actions.py`, `device.py`, `perception.py`, `fsm.py` and `runner.py`. The source still contains the foreground early return, map-idle clock coupling, acceptance-based throw accounting and queue-draining shutdown identified by the audit. The audit's test results are historical evidence, not a fresh test run for this document. No phone behavior or new accuracy result has been measured.

### Ownership

| Role | Owns implementation | Shared-file edit rule |
|---|---|---|
| Lead/integrator | `contracts.py`, public fields in `observation.py`, `effects.py`, shared `Context` fields, `runner.py`, CLI/config composition, event/accounting migrations | Sole editor of these shared integration surfaces. Accepts narrow specialist patches, resolves them, and owns the integrated commit. |
| Reliability | `actions.py`, device adapter, recovery/rotation internals and their focused tests | Does not independently edit lead-owned shared files. Supplies a minimal patch or commit for any required `runner.py`, `Context`, FSM-dispatcher or CLI change. |
| Perception/evaluation | `perception.py`, evidence production internals, annotation/evaluation and extraction tools | Consumes approved `FrameRef`/Evidence fields. A change to public Observation/Evidence shape is proposed to the lead before implementation. No gameplay-policy edits. |
| Gesture/calibration | `gestures.py`, Java/DEX build and calibration tooling; supplies the gesture adapter | PR #2 currently includes an isolated adapter addition in `actions.py`. Reliability owns the integrated `actions.py`/ActionPort implementation; Gesture supplies a narrow adapter patch, and the lead integrates it in T03. No concurrent edits to `actions.py` or Runner. |
| Autoplay | New encounter/stop policy helpers and focused deterministic replay tests | Does not edit public Context, FSM dispatcher, Runner, contracts or event schema directly. Supplies narrow handler/policy patches to the lead for integration after the interface gate. |
| Reviewer | Offline fault/replay harness and independent review | Reports reproducible findings and test requirements; does not edit another owner's implementation. |

These are ownership boundaries, not instructions to launch agents. One owner edits each shared file at a time. Specialists keep changes in their owned files and submit narrow patches when integration surfaces are needed; the lead applies those patches on the integration branch. No parallel branch may redefine a public clock, identity, receipt or evidence field.

## Contract v1.1

**Decision: accept the fail-closed foreground rule.** When foreground is UNKNOWN or stale, no screen-directed action is authorized, including Back and interrupt actions. The Runner must wait for a fresh foreground result or take only bounded non-screen recovery such as a foreground query, restart or halt. This resolves the earlier ambiguity in favor of safety. It is an accepted contract decision. Current PR #3 source/tests now enforce it: UNKNOWN/stale foreground permits no Back, Tap, Swipe or Pinch; only bounded non-screen recovery may proceed. This behavior still requires integration and preservation on the shared runtime branch.

Types live in proposed `pogobot/contracts.py` unless specified otherwise. Frozen dataclasses and string enums are preferred. The following signatures and semantics are the target public API. Existing public constructors remain valid through defaulted optional additions.

### 1. Time, identity and device observations

All time values use one of two explicitly named domains. The type wrappers and suffixes are part of the contract, not optional naming style.

```python
from typing import NewType, Protocol

MonotonicSeconds = NewType("MonotonicSeconds", float)
ActiveSeconds = NewType("ActiveSeconds", float)

@dataclass(frozen=True)
class FrameRef:
    run_id: str
    capture_epoch: int
    seq: int
    captured_mono_s: MonotonicSeconds

class Clock(Protocol):
    def monotonic_now(self) -> MonotonicSeconds: ...
    def active_now(self) -> ActiveSeconds: ...
    def set_paused(self, paused: bool, *, at: MonotonicSeconds) -> None: ...

@dataclass(frozen=True)
class DeviceSnapshot:
    sampled_mono_s: MonotonicSeconds
    foreground: Tristate = Tristate.UNKNOWN
    keyboard: Tristate = Tristate.UNKNOWN
    online: Tristate = Tristate.UNKNOWN
    logical_wh: tuple[int, int] | None = None

class DeviceServices(Protocol):
    def snapshot(self, now_mono_s: MonotonicSeconds) -> DeviceSnapshot: ...
    def close(self) -> None: ...
```

`monotonic_now()` is physical monotonic time elapsed since this run's monotonic origin. It advances during pause. Use it for frame capture/freshness, device sampling and expiry, queue deadlines, action submission/expiry/completion, gesture transport timing and event ordering. It never uses wall time. `active_now()` is the pause-adjusted gameplay clock. It advances at the same rate while running and remains constant during pause. Use it for FSM state age, gameplay cooldowns, map-idle intervals, switch schedules and gameplay deadlines. Resume continues active time without charging the paused interval. There is no direct arithmetic or comparison between the domains; a caller obtains the needed value from `Clock`. Pause transitions pass the single physical timestamp sampled for that loop iteration to `set_paused`.

The run-relative monotonic origin makes serialized timestamps interpretable within `run_id`; it does not make them comparable across runs. Event envelopes store `at_mono_s` and may include `at_active_s` for gameplay events. They never overload an unqualified `at`. Wall time is a separate run-manifest anchor for display only.

`Frame.ts` and `Observation.ts` remain compatibility aliases for the physical capture timestamp; they must equal `frame_ref.captured_mono_s`. FSM time fields, including legacy `Context.now` and `state_since`, are active-domain values until renamed with an `_active_s` suffix. New code uses explicit suffixes and `MonotonicSeconds`/`ActiveSeconds` annotations. Device snapshots, capabilities, ActionPort deadlines/events and gesture submission use physical monotonic values. A paused run cancels/invalidate pending gameplay actions; the worker never compares an active deadline with its physical clock.

Capture identity is assigned once at the capture boundary, before work is submitted to asynchronous perception. Runner creates `run_id` once before capture starts; the capture adapter owns `capture_epoch` and `seq`, increments the epoch on reconnect, source reset or replay seek, and assigns a strictly increasing sequence within that epoch. It places the resulting immutable `FrameRef` on the captured Frame. Perception copies that exact reference onto Observation and each Evidence item. A downstream worker must never stamp a result with whichever epoch is current when it finishes. During constructor migration, `Frame.frame_ref` and `Observation.frame_ref` may be defaulted for old fixtures, but production Runner paths reject missing identity before action submission or evidence emission. Existing `seq/ts` convenience fields must match the attached reference.

Live device snapshots are cached and nonblocking. On timeout, parse failure or expiry, affected facts are UNKNOWN, not FALSE. A positively observed other foreground is FALSE. UNKNOWN or stale foreground does not authorize any screen input, including BACK and actions from interrupts. Recovery may continue only through bounded foreground/restart/halt operations with explicit purpose and policy. Replay supplies snapshots explicitly and must never construct live pollers, resolve display size through ADB, initialize an injector, start an app or query device state.

A request and every derived outcome retain the source `FrameRef`. Capture reset invalidates old actions and temporal perception history. Evidence from a delayed old result cannot resolve an episode after reset, even if the new epoch reuses the same sequence number. A resolved outcome requires evidence from the same run and episode epoch and either post-action evidence or explicitly independent evidence; otherwise resolve UNKNOWN. Do not reconstruct identity from `seq` alone.

Runner receives an injectable `Clock` and `DeviceServices`; CLI composition selects live or fake dependencies explicitly. The same Clock instance is passed to Runner, capture adapters, pollers, ActionPort and GestureExecutor. Unit tests use fake services and clock; the offline harness rejects unprovided live dependencies.

Keep `last_map_ts` for last confirmed map visibility, in the active domain during migration. Add `idle_map_since_active_s: ActiveSeconds | None` and `last_rotation_active_s: ActiveSeconds | None` to Context. Start the idle interval on entry to a confirmed map with no actionable target. Clear it when a target is acquired, map visibility is lost, capture epoch changes or pause occurs. Reset it on an accepted rotation; rejection must not clear spatial memory or mark rotation complete. Repeated map frames do not reset it. A later execution failure triggers recovery/revalidation rather than pretending the camera moved. No rename of `last_map_ts` in the first patch.

### 2. Action requests, receipts and feedback

```python
@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    generation: int
    frame_ref: FrameRef
    submitted_mono_s: MonotonicSeconds
    expires_mono_s: MonotonicSeconds
    effect: object  # validated member of ACTUATIONS
    purpose: str   # target_tap, throw, rotate, stop_spin, recovery, ...
    episode_id: str | None = None

@dataclass(frozen=True)
class Submission:
    action_id: str
    accepted: bool
    reason: str | None = None

@dataclass(frozen=True)
class ActionEvent:
    action_id: str
    generation: int
    status: str
    at_mono_s: MonotonicSeconds
    frame_ref: FrameRef
    reason: str | None = None
    execution_uncertain: bool = False

class ActionPort(Protocol):
    def submit(self, request: ActionRequest) -> Submission: ...
    def poll_events(self) -> tuple[ActionEvent, ...]: ...
    def invalidate(self, generation: int, reason: str) -> None: ...
    def close(self) -> None: ...
```

Runner creates deterministic unique IDs `run_id:action_counter` before submission, records PROPOSED and reserves a pending slot. Each request carries the originating immutable `FrameRef`; submission and expiry use physical monotonic time, while any gameplay deadline remains active time. The worker may publish immediately, but only the runner consumes events on a later tick. One accepted gameplay action may be outstanding per episode. Budgets continue to control pacing; purpose is explicit and is not inferred from human-readable `reason`. Recovery commands have explicit purposes too.

Allowed lifecycle:

* PROPOSED -> REJECTED, with reason such as rate_limited, backpressure, unhealthy, invalid_request or closed.
* PROPOSED -> ACCEPTED -> STARTED -> EXECUTED or FAILED.
* ACCEPTED -> CANCELLED when invalidated, or EXPIRED before worker start.
* Dry-run: PROPOSED -> ACCEPTED -> SIMULATED. SIMULATED is terminal and is never EXECUTED.

Exactly one terminal event per action ID; consumers deduplicate by action ID and status. Resubmitting an identical ID returns its original receipt and never enqueues twice. Reusing an ID with different data is an error. Retain run-scoped receipt/terminal identity until close, or reject IDs older than a documented retention watermark; never silently execute a forgotten duplicate. Preserve worker order per action even when rejection/simulation events originate on the runner thread. An event-delivery overflow must halt admission visibly, never drop completion receipts silently.

EXECUTED means the transport reported successful completion of its command or gesture. It is not proof that the game accepted input, that a ball launched or that a catch occurred. Timeout, interruption or partial transport failure uses FAILED with `execution_uncertain=True` when input may already have reached the phone. Do not automatically retry uncertain execution. A backend must check all stages of composite commands; a successful final shell command cannot mask an earlier failed stage.

`Command.argv` stays internal to actions.py. Rendering must be pure: injector upload/setup moves out of `render()` into explicit live backend preparation. `NullActuator` and real dry-run use the same validation/pacing/lifecycle semantics and emit SIMULATED without subprocess access. Keep `apply(effect, now) -> bool` as a deprecated acceptance-only wrapper during migration; all Runner action paths move to `submit` together. Never add an EXECUTED event to preserve an old dry-run counter.

Runner folds immutable feedback into Context before calling `fsm.step`. Add `pending_action_id: str | None` and `action_feedback: tuple[ActionEvent, ...] = ()` to Context. Feedback is limited to the current tick; the controller's explicit phase/pending ID retains necessary state. Late feedback remains traceable and releases resources but cannot advance a newer generation or episode. Workers never call FSM handlers or mutate Context.

### 3. Dependent effects and cancellation

Add `Dispatch` to effects.py and Effect, without classifying it as a raw actuation:

```python
@dataclass(frozen=True)
class Dispatch:
    action: object  # one ACTUATIONS member
    purpose: str
    on_accepted: tuple[object, ...] = ()
```

The runner stamps an ActionRequest and submits it once. Apply `on_accepted` only after a positive receipt. Initially allow only SetIntent, SetFlag, Cooldown, ClearSpatialMemory and Transition in that tuple, with validation against a fixed allowlist. Nested Dispatch, additional actuation and external effects are invalid. Existing unconditional transitions remain legal for independently observed screen changes. Replace every actuation-dependent sibling effect group, including target tap/intent/transition and camera rotation/spatial clear. Acceptance permits entry into a pending state; only feedback and new observations permit claims about execution or results. Rejection leaves intent, state, cooldown and success flags unchanged.

Runner owns `action_generation`. Increment/invalidate on pause, halt, foreground loss/unknown expiry, capture loss/reset, app restart, account-context change, display geometry change and an unexpected screen/episode change. Expected phase changes inside the same episode do not cancel the action being awaited. Resume gets a fresh generation and fresh observations, with no pending gameplay command restored.

`invalidate(new_generation, reason)` atomically advances the worker's current generation. Worker rechecks generation, deadline, capture epoch and cached input permission at the serialized start boundary. Every queued stale request receives CANCELLED/EXPIRED. Admission and the start boundary must share synchronization with invalidation: if invalidation wins, the command cannot start. If start wins, the action is in flight and cannot be promised retractable. In-flight cancellation requests backend stop/release, then emits an honest terminal result, marking uncertainty as needed. Block replacement gameplay actions until cleanup/revalidation. Opaque ADB swipes have only bounded best-effort cancellation; document that limit explicitly. Do not claim pause can undo input already delivered.

Close stops admission, invalidates queued requests, attempts in-flight cleanup and joins within a defined backend timeout. It must never flush queued gestures to the phone. Recovery attempts have their own bounded counters; accepted attempts still consume an attempt even if later failure requires escalation.

### 4. Perception evidence and game outcomes

```python
@dataclass(frozen=True)
class Evidence:
    frame_ref: FrameRef
    kind: str
    value: Tristate
    confidence: float | None
    source: str
    region_xyxyn: tuple[float, float, float, float] | None = None

@dataclass(frozen=True)
class GameOutcome:
    outcome_id: str
    episode_id: str
    kind: str
    evidence_level: str  # observed, inferred, unknown
    evidence: tuple[Evidence, ...]
    action_id: str | None = None
```

Add trailing `frame_ref: FrameRef | None = None` and `evidence: tuple[Evidence, ...] = ()` to Observation, leaving existing constructors and signals intact during migration. Production observations must carry the exact Frame reference; missing or mismatched identity blocks actions and evidence. Fixed initial evidence names: `ball_ready`, `ball_launched`, `ball_in_flight`, `target_attacking`, `catch_confirmed`, `flee_confirmed`, `breakout_confirmed`, `out_of_balls`, `stop_disc_visible`, `stop_reward_visible`. Missing evidence means UNKNOWN, never false. `confidence=None` means no probabilistic score, not zero confidence. Include source/model/version in the trace manifest. All regions use normalized stream coordinates; raw optical fractions remain available in existing Signal fields. Each Evidence uses the originating `FrameRef`; compatibility `seq/ts` values, if retained, are derived from that reference and cannot be independently set. Add `aim_region` as evidence only when actually measured and validated; do not repurpose a generic detection box as a calibrated throw target.

Perception emits facts without episode IDs or gameplay policy. Runner/controller associates temporally relevant evidence with the active episode. A GameOutcome requires post-action or explicit independent outcome evidence in the current capture epoch. A map return alone cannot distinguish catch, flee and user exit. An executed swipe alone cannot prove ball launch. Unknown outcomes resolve a bounded episode without incrementing catch/reward totals. Outcome IDs deduplicate persistent stats and quota updates. Observed positive and inferred results are counted separately. Conflicting positives remain unknown pending bounded re-observation. Stabilization counts distinct sequences, requires its full warm-up vote and resets on discontinuity. Evidence thresholds and temporal windows must be selected from data, not invented in this contract.

### 5. Provider and gesture interfaces

```python
@dataclass(frozen=True)
class Capability:
    name: str
    availability: str  # supported, unsupported, unknown
    enabled: Tristate
    checked_mono_s: MonotonicSeconds
    reason: str

class Provider(Protocol):
    def capabilities(self, now_mono_s: MonotonicSeconds) -> tuple[Capability, ...]: ...

@dataclass(frozen=True)
class Sample:
    t_ms: int
    x: float
    y: float

@dataclass(frozen=True)
class Trajectory:
    samples: tuple[Sample, ...]
    seed: int

@dataclass(frozen=True)
class Calibration:
    screen_wh: tuple[int, int]
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

class PointerSink(Protocol):
    def down(self, x: int, y: int) -> None: ...
    def move(self, x: int, y: int) -> None: ...
    def release(self, x: int, y: int, *, cancelled: bool) -> None: ...

class GestureExecutor:
    def __init__(self, sink: PointerSink, clock: Clock): ...
    def execute(self, trajectory: Trajectory, calibration: Calibration,
                cancel: Event, *, submitted_mono_s: MonotonicSeconds | None = None,
                screen_wh: tuple[int, int] | None = None) -> Execution: ...
```

Initial provider capabilities: `auto_spin`, `auto_battle`, `autowalk`. Distinguish provider support from enabled state; require fresh positive support and enabled evidence before relying on assisted behavior. Unknown blocks that dependent feature and produces a reason. A standard-client adapter declares unsupported provider features and can still use implemented manual touch actions. Capability snapshots are read-only inputs in Context; providers never actuate from `capabilities()`.

`Trajectory` and `Calibration` live in `pogobot.gestures`; they are pure data and the calibration is separate from the planned capture-normalized path. `PointerSink` and `GestureExecutor` define opt-in continuous-pointer execution. Keep this executor disconnected from gameplay until it is routed through the action lifecycle. Then add `TraceGesture(trajectory, reason, budget="throw")` to effects.py and ACTUATIONS only after its backend is integrated and validated. GestureExecutor receives the shared Clock and records queue/submission times as `MonotonicSeconds`; remove its default direct `time.perf_counter` clock and rename `submitted_at` to `submitted_mono_s`. Coordinates are finite normalized fractions in [0,1], origin top-left. Samples require at least two entries, first t_ms=0, subsequent times strictly increase, and a validated maximum duration/sample count. Backend emits one continuous down/move/release contact, releases on normal completion, and attempts release in failure/cancellation cleanup. No sequence of independent swipes qualifies. Convert to logical device pixels once using Calibration and reject stale viewport geometry. Unsupported trajectories return an explicit rejection; straight Swipe remains available where the caller explicitly selects a supported fallback.

Pure planner API in gestures.py: `plan_straight(start, end, *, duration_ms, interval_ms, safe_area, target_area, variation, seed, max_velocity) -> Trajectory`. Calibration fits per-axis scale and offset from measured normalized anchors and stores logical display size; recalibrate after geometry changes. Seeded variation stays inside the supplied target and safe rectangles and never compensates for uncertain perception. Keep queue delay, transport delay and on-device gesture duration separate in logs. The executor requires a bounded, synchronous PointerSink; timeout or release failure remains visible and execution is uncertain. Curve planning and performance claims wait for backend validation. Smooth motion is a quality objective; it provides no basis for a reduced-ban-risk claim.

### 6. Event schema and migration

Emit versioned event envelopes: `schema_version=2`, `event_id`, `run_id`, `capture_epoch`, physical monotonic-relative `at_mono_s`, optional pause-adjusted `at_active_s`, `type`, optional `action_id`, `episode_id`, `generation`, `frame_ref`, and typed `payload`. No unqualified timestamp field is permitted. Distinguish action events from game outcomes. Include run configuration/model hashes and timestamp anchor once in a run manifest. Do not log credentials, raw account names or notification secrets.

New counters: `throws_proposed`, `throws_accepted`, `throws_executed`, `ball_launches_observed`, `catches_observed`, `catch_outcomes_unknown`, `stop_spins_executed`, `stop_rewards_observed`, plus failure/cancellation and simulation counters. Deduplicate launches/rewards across repeated frames, not just command completions. Ratios state their numerator, denominator and sample counts.

Preserve old saved files; read missing schema_version as v1 and retain legacy counters under clearly marked legacy/inferred fields. Never reinterpret historical balls-thrown values as executed throws, or historical stop visits as rewards. V2 writes are additive initially; deprecate ambiguous display labels when consumers migrate. Keep local configured quota conservative by reserving accepted stop attempts and recording confirmed rewards separately; an unknown result must not free a reservation merely because no reward was seen. Simulated/inferred events do not increment confirmed totals. Prove migration with representative saved records before changing stats/quota writers.

## Current PR reconciliation and merge gates

The exact PR heads were rechecked immediately before this refresh: #1 `3f8172846a4214e3a98c6058f4835e0a379b8171`, #2 `c6fcf83eea25b2916402c238e5b44feac72b4a49`, #3 `1dbfe97be720e94b48150ed561d31ef7df548d8f`, and #4 `877757c9e1a6afac1b9a13e41ccc8639b68ac3eb`. All are open against `main`; none is merged. Updating this plan advances PR #1 again. Its resulting head is the commit returned by this update, so this document cannot self-reference that new SHA. Recheck PR metadata before relying on this plan.

**PR #1 mergeability check:** repeated metadata reads have alternated between `mergeable=false` and `mergeable=true` for the same head; the last pre-refresh read on `3f8172846a4214e3a98c6058f4835e0a379b8171` returned false. `compare_commits` on that input head reports 22 commits ahead of `main`, zero behind, merge base equal to the PR base SHA, and exactly one changed file (`docs/IMPLEMENTATION_BACKLOG.md`, added). No conflicting file or divergent base is shown. The false value is not explained by the available diff/compare evidence and appears to be a transient or stale mergeability calculation; this is not proof of a content conflict. No status checks were reported. If GitHub continues to report false after its state settles, inspect its conflict details/UI before any merge. Do not treat this observation as a merge instruction.

### PR head and test-evidence recheck

| PR/head | Current source/status | Test evidence |
|---|---|---|
| #1 `ef160123` (pre-refresh input head) | Contract v1.1 and integration plan only; docs-only. | No runtime tests apply. Every shared API remains a proposal until its implementation ticket is integrated and accepted. This plan refresh advances #1. |
| #2 `c6fcf83` | Isolated gesture planner/calibration, injector, transport, tests and Android smoke procedure. Not connected to gameplay ActionPort. | Focused offline run: **27 passed** (independent result supplied for this reconciliation; PR body also lists 27). PR body command: `PYTHONPATH=/root/.local/lib/python3.12/site-packages python -m pytest tests/test_gestures.py tests/test_gesture_transport.py tests/test_gesture_dex.py -q`. Synthetic DEX tests do not prove a real D8 build. No Android input was run. |
| #3 `1dbfe97` | Runtime recovery/rotation, nonblocking foreground and UI-tree polling, async home navigation. Current source includes fail-closed UNKNOWN tests and the Runner.run-loop post-teleport notifier regression `test_run_loop_polls_unknown_home_navigation_and_reports_once_without_retry` in `tests/test_go_home.py`. | Reported focused offline run at this head: **115 passed**: `python -m pytest tests/test_go_home.py tests/test_foreground_recovery.py tests/test_runtime_reliability.py tests/test_fsm_livelocks.py -q`. This gate includes the Runner notifier regression and fail-closed UNKNOWN foreground coverage. PR-description's **990 passed, 39 skipped** from `python -m pytest tests --ignore=tests/test_sponsored_ad.py -q` remains a PR-description claim, not independently verified at this head. |
| #4 `877757c` | Offline detector/outcome evaluation, reviewed promotion and extraction safeguards, README and held-out corpus protocol. No runtime FrameRef/outcome integration. | Focused offline run at current head `877757c9e1a6afac1b9a13e41ccc8639b68ac3eb`: **23 passed**, `python -m pytest tests/test_evaluation.py -q` (reported independent run). PR body still says 11 passed and is stale. No reviewed corpus or accuracy result exists. |

The focused counts above are offline-only reports, not Android/device, live gameplay, or accuracy evidence. GitHub combined-status queries returned no status checks for #1, #3 or #4; #2's 27-pass result is recorded in its PR description. These counts are focused offline test runs, not GitHub CI statuses. The PR #3 notifier regression is present and selected by the exact 115-pass gate above. PR-description's 990/39 broad suite count remains a claim, not rerun evidence. The contradictory old UNKNOWN behavior is also corrected in current #3 source: its FSM test expects no Back/Tap/Swipe/Pinch for UNKNOWN and retains bounded non-screen restart escalation. Keep that behavior as required policy; do not restore the old expectation.



### PR #1 mergeability investigation

At current PR #1 head `d712756bc9ff356960c6a2e73802e61b3527ec1f`, GitHub metadata reports `mergeable=false`. Its `main` comparison is `ahead` by 29, `behind` by 0, with merge base `9795280ec52b67cde316c45aa531e64c39ddd9b8`; the only changed path is the added `docs/IMPLEMENTATION_BACKLOG.md`. At prior head `480ccf22752b6418554f2fa4c27dbea2b84f4802`, metadata returned false once and true twice without a head change. A clean path comparison does not expose a content conflict, but this connector response does not provide the detailed conflict reason, so the current false value's precise cause is unresolved. Check GitHub's merge-conflict details before treating it as a source conflict.

### Current blockers to integration testing

| Gate | Owner | Blocker | Command/evidence to clear |
|---|---|---|---|
| Integrated offline test | Lead integrates; Reliability, Gesture, Perception supply owned patches | A T01 branch candidate exists but is partial and untested. Runner/CLI clock injection, DeviceServices runtime wiring, ActionPort/Dispatch, runtime Evidence/GameOutcome and v2 event migration remain proposals or unimplemented integration work. #2's `actions.py` adapter needs a narrow handoff into Reliability-owned ActionPort work; no overlapping edits. Focused PR runs do not equal an integrated suite. | Build the integration candidate from the common base after T01–T07, then run exactly `python -m pytest tests -q` with all test dependencies installed and no blanket exclusions. Preserve candidate SHA, full command/output, pass/fail/skip counts, dependency inventory and logs. A pass is required; any missing optional dependency or skip must be reported and dispositioned, not silently excluded. |
| Supervised Android injector smoke | Gesture runs; Lead reviews | No real D8 artifact has been built/inspected and no supervised Android input evidence exists. | At current #2 head run `bash -n tools/gesture/build.sh`, `bash tools/gesture/build.sh`, verify generated DEX class definitions, then execute `tools/gesture/ANDROID_SMOKE_TEST.md` on the supervised touch-logging non-game surface. Save artifact SHA-256, toolchain/device details and event log/checklist. |
| Supervised gameplay test | Lead coordinates; all feature owners | Blocked by integrated offline gates, ActionPort and FrameRef/evidence wiring, T08/T09 deterministic replay, and supervised gameplay trace. Android injector smoke is a separate prerequisite, not a gameplay result. | Clear T01–T10 implementation/replay criteria, separately pass the Android smoke, then conduct the bounded attended gameplay protocol below. Preserve commit/config/device, frame-action-evidence trace, notifier/intervention records and observed outcomes. No accuracy claim follows from smoke or one session. |

### Next integration candidate

The next candidate is **T01 only**, cut from common `main` at `9795280ec52b67cde316c45aa531e64c39ddd9b8`; branch `integration/t01-clock-frame-ref` is in progress at `bc7d1f4311a2d783c28b21415ee13129167fed94`. It is not an integration of the four PRs. The Lead owns `contracts.py`, public `Frame`/`Observation` fields, capture-to-observation identity propagation, shared `Context`/Runner and composition; Reliability supplies the device adapter as a narrow patch and owns its internals. No specialist edits to lead-owned shared files.

Candidate slice implemented: clock types and `SystemClock`; optional `FrameRef` fields; identity stamping by current live/replay capture sources; propagation by `Perceptor.observe`; focused tests in `tests/test_clock_contract.py` and `tests/test_frame_identity.py`. These definitions and fields remain proposals until candidate tests pass and interface review accepts them. T01 is incomplete: Runner/CLI clock injection, cached live `DeviceServices`, explicit fake/replay composition and zero-external-call proof, capture epoch reset/reconnect behavior, and full offline integration are not implemented. Run `python -m pytest tests/test_clock_contract.py tests/test_frame_identity.py -q`; this candidate command has not yet been run. Then run `python -m pytest tests -q` on the assembled candidate. Do not claim T01 complete based on PR #3's poller tests or PR #2/#4 focused runs.

### Proposed branch order

This is the ordered integration sequence, not a merge instruction. Input heads checked for this refresh are #1 `ef160123a8cf5dccc73656762683f394cf68949d`, #2 `c6fcf83eea25b2916402c238e5b44feac72b4a49`, #3 `1dbfe97be720e94b48150ed561d31ef7df548d8f`, and #4 `877757c9e1a6afac1b9a13e41ccc8639b68ac3eb`. PR #1 remains the proposed contract source. Fail-closed UNKNOWN foreground is the accepted policy and is present in PR #3 source/tests; the current focused gate includes the notifier regression and reports 115 passed.

1. **Contract review (#1)**: accept v1.1 signatures and invariants; record approval. No behavior is implemented by this documentation step.
2. **T01 shared runtime seam (Lead owns shared files; Reliability contributes device adapter)**: implement dual clocks, DeviceServices, capture-stamped FrameRef through Frame/Observation/Evidence, replay isolation and baseline CI. PR #3 adapts to these seams.
3. **T02 runtime recovery and home-navigation acceptance (#3; Reliability owns internals, Lead owns Runner/shared edits)**: run the exact focused Runtime gate on #3's head. Confirm fail-closed foreground at public FSM and Runner paths and execute the Runner.run-loop notifier regression against unavailable/stale/timed-out post-teleport outcomes. One notification, no success, no guessed tap and no retry. Integrate only after recorded test evidence; #3's current source already contains the policy change and notifier test.
4. **T03 shared ActionPort (Reliability owns `actions.py`/device internals; Lead owns contracts, Effects, Context and Runner)**: adapt #2's isolated transport through a narrow patch. Implement ActionRequest/Submission/ActionEvent, Dispatch, terminal receipts, generations, cancellation and conditional effects. Gesture does not edit shared runtime files.
5. **T04 event/accounting migration (Lead)**: implement v2 event timestamps and accounting after T03, with v1 read compatibility and deduplication.
6. **T05 reviewed data/evaluation gate (#4; Perception)**: integrate class-aware evaluation, review/hash/split safeguards and corpus collection requirements after T01. This remains offline and makes no accuracy claim.
7. **T06 Gesture ActionPort adapter (#2; Gesture + Reliability)**: keep pure planning isolated; wire the transport only after T03, consuming its FrameRef, physical Clock and cancellation generation. Run adapter tests. The Android smoke remains separate and can be performed earlier on a non-game surface after real D8 output is built.
8. **T07 runtime-linked outcome evidence (#4; Perception owns evidence producer, Lead integrates public Observation/Evidence/Runner fields)**: after T03 and T05, propagate capture FrameRef and reject old-epoch evidence.
9. **T08 encounter/provider policy (# Autoplay; Lead integrates shared surfaces)**: only after T03, T04, T06 and T07, plus explicit interface-freeze acceptance.
10. **T09 stop/restock (# Autoplay; Lead integrates)**: after T08, with explicit unknown and quota behavior.
11. **T10 fault/replay review (# Reviewer + Lead)**: after T09; clear integrated offline and Android smoke gates before the later gameplay test.

No Autoplay PR is open at these heads. Autoplay may write isolated policy tests against the frozen interfaces, but must wait for explicit acceptance of the shared interfaces listed below before production implementation.

### Autoplay interface-freeze gate

Before Autoplay implements encounter or stop policy, the lead, Runtime, Gesture, Perception and Autoplay owners must accept these interfaces and semantics in review. Acceptance means the signatures and invariants in Contract v1.1 are stable; implementation dependencies below must also be merged before production integration.

| Interface | Required agreement before Autoplay codes against it | Implementation gate |
|---|---|---|
| Clock and capture identity | `MonotonicSeconds` is physical and advances during pause; `ActiveSeconds` freezes during pause. `FrameRef(run_id, capture_epoch, seq, captured_mono_s)` is immutable, assigned at capture, and copied unchanged through Observation/Evidence/ActionRequest/ActionEvent. Missing, stale, or prior-epoch identity fails closed. | #1 contract review, then #3/T01 tests. |
| Action lifecycle | Exact ActionRequest/Submission/ActionEvent/ActionPort signatures; one terminal receipt per action ID; ACCEPTED is not EXECUTED; uncertainty is explicit; one outstanding gameplay action per episode; generation invalidation and serialized start-boundary rules; Dispatch applies dependent effects only after acceptance. | T03 implementation and cancellation/fault tests. |
| Outcome evidence | Evidence carries the originating FrameRef; GameOutcome identifies episode, evidence level and source action; only same-run/current-epoch post-action or explicitly independent evidence resolves an outcome; missing/conflicting evidence resolves UNKNOWN. Perception emits facts, Runner associates episode. | #4 T07 after T03 and T05. |
| Event/accounting schema | Physical ordering uses `at_mono_s`, optional gameplay time is `at_active_s`; simulated, accepted, executed, uncertain and outcome events stay distinct; IDs deduplicate and legacy data is labelled. | T04 migration and round-trip/deduplication tests. |
| Gesture/provider inputs | Trajectory/calibration are pure values; GestureExecutor receives the shared Clock and T03 cancellation/action context; pointer release and uncertainty are specified. Capability freshness uses `checked_mono_s`; unknown/unsupported provider state cannot authorize a dependent feature. | #2/T06 adapter tests; provider contract implemented with T08. |

### Focused release test matrix

These tests target release-blocking failure semantics. Use deterministic fake clocks, readers and action ports with synchronization barriers; wall-clock sleeps must not serve as the ordering assertion.

| Failure class | Required test scenario | Exact pass condition | Owner / gate |
|---|---|---|---|
| Rejected action | Reject by rate limit, full queue, breaker, closed port, and actuator refusal for rotation. Exercise dependent intent/transition/cooldown effects. | One terminal REJECTED receipt per request; no EXECUTED count, intent, dependent transition or success flag. Rejected rotation retains cooldowns and progress. | Runtime now; ActionPort cases T03. |
| Stale observation | Deliver a frame after its freshness deadline; reset capture and reuse seq=1, then deliver the old asynchronous observation/evidence; invalidate a UI-tree query after an input/state change and deliver its late result. | No stale, mismatched or prior-epoch frame can submit a screen action or resolve a new outcome. A late UI-tree result cannot authorize a tap. UNKNOWN/stale foreground blocks Back/Tap/Swipe/Pinch while bounded restart/halt remains available. | Runtime public-path tests; FrameRef tests T01/T03/T07. |
| Cancellation | Hold action A in transport, queue B, invalidate by pause/halt/capture reset/episode change, then release A. Race invalidation against start using barriers. Close with queued work. | If invalidation wins, B never starts and ends CANCELLED/EXPIRED. If start wins, result marks in-flight uncertainty honestly. Close never flushes queued input. No blind retry of uncertain execution. | T03 ActionPort/fault tests; T06 adapter tests. |
| Unknown outcome | Encounter action accepted/executed but no fresh distinguishing result; async home navigation receives unavailable/stale/timed-out tree after teleport; evaluator receives unknown prediction. | Encounter does not throw again or count a catch/reward. Navigation returns UNKNOWN/failure, never success, notifies once and performs no guessed tap/retry. Evaluation records abstention, not a positive outcome. | Autoplay T08 requires T03/T07. Home navigation is a current #3 release gate. |

### Android injector smoke gate

This is a separate, isolated device test. It does not wait for T01/T03 and must not open Pokémon GO. It validates only PR #2's injector on a supervised Android device and a disposable touch-visualization surface.

Entry criteria:

1. Run `python -m pytest tests/test_gestures.py tests/test_gesture_transport.py tests/test_gesture_dex.py -q` and `bash -n tools/gesture/build.sh` at current #2 head c6fcf83; then run `bash tools/gesture/build.sh` with a real JDK and Android SDK Build Tools installation.
2. Run the DEX checker on the produced artifact and verify it defines Gesture, Gesture$Point and Gesture$MotionAction. Record the artifact SHA-256 and tool versions. Synthetic DEX fixtures do not pass this criterion.
3. Use an authorized test device with ADB, recorded model/Android API/build fingerprint/display geometry/serial, an operator present, and the non-game surface visibly logging pointer ID, action, coordinates, downTime and eventTime. Agree timing and coordinate tolerances before observing results.

Acceptance, following tools/gesture/ANDROID_SMOKE_TEST.md:

- Startup sends READY within the configured bound and emits no touch before a valid plan. A blocked/unavailable startup sends no plan and cleans up process and pipes within the cleanup bound.
- At least five in-bounds plans each produce exactly one DOWN, ordered MOVE events and exactly one UP for pointer 0; pointer count and ID stay one/constant; downTime stays constant; eventTime strictly increases; coordinates are within pre-agreed tolerance; START/DONE receipts and process exit are successful.
- Cancellation after DOWN and a MOVE produces one CANCEL and no later MOVE/UP; cancellation before a plan produces no DOWN. A subsequent supervised plan starts cleanly.
- Rejection, suppressed terminal receipt/timeout and any tested disconnect fail visibly, close pipes/processes, release the pointer and never report success. Record each check and timing result; any missing/extra event, stuck pointer, unexpected app interaction or tolerance failure fails the smoke.

This test may run before gameplay integration because the injector remains disconnected. Passing it validates only the recorded injector/device/test-surface combination. It is not evidence of gameplay, outcome accuracy or permission to wire the transport into Runner.

### Supervised gameplay test gate

Do not begin gameplay testing until these ordered gates pass:

1. Accept the shared v1.1 signatures and this fail-closed UNKNOWN policy. Interface acceptance freezes semantics only; each proposal remains unimplemented until its owning ticket passes.
2. Integrate T01 and prove dual-clock pause behavior, capture-time FrameRef assignment/propagation, stale/old-epoch rejection and device-free replay.
3. Close #3/T02 from current PR #3 head `1dbfe97` or a reviewed follow-up: `python -m pytest tests/test_go_home.py tests/test_foreground_recovery.py tests/test_runtime_reliability.py tests/test_fsm_livelocks.py -q` passes and record the fail-closed tests plus the Runner.run-loop notifier regression for unavailable/stale/timed-out post-teleport home results; notification is once-only, with no success claim, guessed tap or retry. Public FSM/Runner tests prove UNKNOWN/stale foreground emits no Back/Tap/Swipe/Pinch in normal, recovery and interrupt paths; bounded non-screen recovery remains possible. Run focused Runtime tests and the full available suite, recording exact commit, command and pass/fail/skip counts.
4. Integrate T03 and pass rejection, stale-frame, cancellation/start-race, uncertain execution, deduplication and dry-run tests. Integrate T04 and pass v2 round-trip, physical ordering, active-time accounting and legacy-label tests.
5. Integrate T06 against ActionPort/FrameRef/Clock/cancellation contracts and pass the isolated Android injector smoke above. Integrate T05/T07; runtime evidence preserves capture FrameRef, old-epoch evidence cannot resolve an episode, and conflicting/unknown evidence abstains.
6. Pass T08/T09 deterministic replay for catch, flee, breakout, attack, no-balls, timeout, cancellation and unknown. Assert bounded actions, no retry after uncertain execution, no unknown-to-success promotion, no unsupported restock/purchase, and functioning quota/health stop.

Then run one short, attended gameplay session on an explicitly selected test profile/device. Before any screen input, require fresh foreground=TRUE and a fresh matching FrameRef; UNKNOWN, stale or mismatched identity stops screen actions. Log each request, receipt, frame/evidence identity, notifier and terminal outcome. Bound duration and action count, keep an operator present, and test the stop control before play. Test the smallest supported path, with at most one outstanding throw per encounter, and wait for distinguishing evidence before any follow-up. Unknown or uncertain results stop or abstain, never trigger a blind retry. Abort on rejected/uncertain action, unexpected screen, notifier failure, missing evidence, stuck input or foreground/identity violation. Review the trace before any longer session. Record device/build/config/commit IDs, failures and interventions. This is a supervised functional test only, not an accuracy or unattended-readiness result.

### Contract field status at rechecked PR heads

The #1 v1.1 APIs below are proposals unless explicitly marked as existing specialist implementation. “Accepted policy” means the rule is agreed; it does not make the proposed shared runtime API implemented. PR heads were rechecked before this plan refresh; #1's input head was `3f8172846a4214e3a98c6058f4835e0a379b8171`. Current #4 is `877757c9e1a6afac1b9a13e41ccc8639b68ac3eb`; #1 advances with this update.

| Contract area | Status |
|---|---|
| `MonotonicSeconds`, `ActiveSeconds`, `Clock`, `DeviceSnapshot`/`DeviceServices`, pause mapping | Proposal in #1. T01 candidate implements the type definitions and `SystemClock`, but no Runner/CLI injection or DeviceServices adapter is integrated or accepted. #3 has nonblocking pollers and legacy time handling, not an end-to-end dual-clock seam. |
| Immutable `FrameRef` at capture, propagated through Frame/Observation/Evidence/ActionRequest/ActionEvent | Proposal in #1. T01 candidate stamps live/replay Frames and propagates through Observation; candidate tests are added but unrun. Evidence/ActionRequest/ActionEvent propagation and production missing-identity rejection remain unimplemented. Current #3 has no end-to-end FrameRef path. |
| `ActionRequest`, `Submission`, `ActionEvent`, `ActionPort`, `Dispatch`, generation invalidation and cancellation | Proposal in #1. #3 uses `apply(effect)->bool`; #2 has isolated transport receipts, not the common ActionPort. |
| `Evidence.frame_ref`, runtime `GameOutcome` association, episode-safe outcome rules | Proposal in #1. #4 has offline scoring/extraction, not shared runtime evidence identity or outcome association. |
| V2 event envelope and accepted/executed/uncertain/outcome accounting | Proposal in #1. No integrated v2 runtime migration. |
| Fail-closed UNKNOWN/stale foreground behavior | Accepted integration policy. Implemented in current #3 source/tests (FSM and Runner guards); still must be integrated and preserved on the shared runtime branch. |
| Runtime reliability and async home navigation | Implemented in #3 source. Current source contains the Runner.run-loop unavailable-result notifier test and fail-closed UNKNOWN tests. Focused offline run is reported as 106 passed, but exact command/test selection was not supplied; record targeted execution evidence before clearing T02. PR-description 990/39 remains a broad-count claim unless rerun at current head. |
| Gesture planner/calibration/injector/isolated transport | Implemented in #2 source/tests as isolated components; not connected to common ActionPort, FrameRef or Clock. Independent focused offline run: 27 passed. Real D8 output and Android input are unverified. |
| Evaluation, reviewed promotion and extraction tools | Implemented in #4 source. Independent focused offline run at current head `877757c9e1a6afac1b9a13e41ccc8639b68ac3eb`: 23 passed with `python -m pytest tests/test_evaluation.py -q`; PR body says 11 and is stale. No reviewed corpus or accuracy result. |
| Provider capability interface/consumer policy and Autoplay encounter/stop loops | Proposal in #1; no current Autoplay implementation PR. |


## Ordered tickets

Dependencies are integration prerequisites. Each ticket is a bounded implementation change with tests added before/alongside its fix. Proposed signatures below do not authorize implementation until the contract review gate passes. Shared-file ownership follows the matrix above.

| Order | Ticket / owner | Dependencies | Exact focused command (run after the ticket adds listed tests) | Acceptance evidence |
|---|---|---|---|---|
| 1 | T01 Dual-clock/device/FrameRef seam — Lead; Reliability supplies device adapter | Contract v1.1 review | `python -m pytest tests/test_clock_contract.py tests/test_device_services.py tests/test_frame_identity.py -q` | Deterministic pause/resume, device-free replay, stale snapshot, capture-stamped identity and reused-sequence delayed-result tests all pass; report exact output and full offline baseline. |
| 2 | T02 Recovery/rotation/home navigation — Reliability; Lead integrates Runner/shared changes | T01; current #3 source is the behavioral input | `python -m pytest tests/test_go_home.py tests/test_foreground_recovery.py tests/test_runtime_reliability.py tests/test_fsm_livelocks.py -q` | UNKNOWN/stale foreground emits no screen input on FSM and Runner paths while bounded non-screen restart/halt remains; current Runner.run-loop notifier test proves unavailable/stale/timed-out post-teleport result causes exactly one notification, no success, no guessed tap/retry. Accepted/rejected rotation, pause invalidation, timeout and shutdown cancellation pass. Record selected test names and count. |
| 3 | T03 ActionPort/Dispatch/cancellation — Reliability owns `actions.py`; Lead owns shared contracts/effects/Context/Runner | T01, T02 | `python -m pytest tests/test_action_port.py tests/test_action_dispatch.py tests/test_action_cancellation.py tests/test_runner_actions.py -q` | Rejection has one terminal receipt and no dependent effects; stale frame/epoch cannot submit; cancellation races are synchronized; queued work is not flushed on close; uncertain execution cannot be retried/count as executed; FrameRef and physical timestamps survive requests/events. |
| 4 | T04 V2 event/accounting migration — Lead | T03 | `python -m pytest tests/test_event_migration.py tests/test_accounting_receipts.py -q` | V1 records round-trip as marked legacy; V2 events use `at_mono_s` and optional `at_active_s`; deduplication works; rejected/simulated/uncertain actions never count as executed or confirmed outcomes. |
| 5 | T05 Reviewed detector evaluation/data gate — Perception | Contract review, T01; PR #4 current focused source | `python -m pytest tests/test_evaluation.py -q` | Hash-bound approvals, recording/session split integrity including omitted recordings, class-aware duplicate/wrong-class matching, collision-safe extraction and abstentions pass; no accuracy statement without reviewed corpus. Reconcile run output with reported 23 focused offline passes. |
| 6 | T06 Gesture planner and ActionPort adapter — Gesture; Reliability reviews adapter | Contract review; T03 for transport; T01 for FrameRef/Clock | `python -m pytest tests/test_gestures.py tests/test_gesture_transport.py tests/test_gesture_dex.py tests/test_gesture_action_adapter.py -q` | Bounded deterministic plans; shared physical Clock and ActionPort cancellation identity; release on all exits; stale generation rejection; uncertain transport visible. Real D8/device evidence is a separate smoke gate. |
| 7 | T07 Runtime-linked outcomes/evidence — Perception owns producer; Lead integrates shared types/Runner | T03, T05 | `python -m pytest tests/test_outcome_evidence.py tests/test_temporal_perception.py tests/test_frame_identity.py -q` | Evidence keeps exact FrameRef; old epoch cannot resolve a new episode; conflicting/missing evidence yields UNKNOWN; only post-action/current-epoch or explicit independent evidence resolves. |
| 8 | T08 Encounter/provider capability — Autoplay; Lead integrates shared fields | T03, T04, T06, T07 and interface freeze | `python -m pytest tests/test_autoplay_replay.py -q` | Catch/flee/breakout/attack/no-balls/unknown traces are deterministic; one outstanding throw; no retry without fresh readiness/evidence; unsupported or unknown capability blocks dependent feature. |
| 9 | T09 Stop/reward/restock — Autoplay; Lead integrates | T08 | `python -m pytest tests/test_stop_replay.py tests/test_quota_accounting.py -q` | Stop visits do not count as rewards; unknown reward retains reservation; retries bounded; no unsupported purchase/restock; observed reward is distinct from accepted visit. |
| 10 | T10 Fault/replay milestone review — Reviewer + Lead | T01–T09 | `python -m pytest tests/test_fault_replay.py tests/test_runtime_reliability.py tests/test_go_home.py -q` plus `python -m pytest tests -q` | Rejected action, stale observation, cancellation race, unknown outcome and shutdown scenarios pass; full integrated offline suite reports SHA, exact command, dependencies and pass/fail/skip counts. Then run the separate device and gameplay gates. |

### T01: establish a hermetic baseline

Scope: add Clock/DeviceServices seams, live cached foreground polling and deterministic fake snapshots. Route runtime foreground, online, size and keyboard reads through composition; ensure replay startup and teardown avoid live services. Add a fast offline CI job and separately classified vision/corpus checks. Do not upgrade or lock all dependencies as an incidental change.

Acceptance:

1. Construct/run/close Runner with fake source, perceptor, action port, Clock and device services while subprocess and network access are blocked. Exercise pause/resume and replay with zero external calls.
2. Advance physical time by 60 seconds while paused: active time and gameplay deadlines do not advance; frame age, poll expiry and physical action deadlines remain nonnegative and correctly ordered. A post-resume action is not immediately expired by a worker. Use one sampled physical timestamp for each pause transition.
3. Assert frame freshness uses only physical time and gameplay timers only active time. Fake time deterministically reproduces both; repeated scenarios produce identical decisions/events.
4. FrameRef is stamped before perception; Observation and Evidence retain the same run_id/capture_epoch/seq. After capture reset, reuse seq=1 and deliver a delayed old result: it cannot submit an action or resolve the new episode.
5. Foreground timeout returns UNKNOWN; confirmed other app returns FALSE; stale snapshots expire. Frame loop does not wait on the slow query. UNKNOWN in every public FSM path authorizes no screen input, including interrupts, while bounded recovery can log restart/halt.
6. Run `python -m pytest tests -q` and report exact failures/skips. Missing packages/corpora are explicit capabilities in split jobs, never hidden blanket skips. Record the new baseline rather than copying old counts.

Likely files: new contracts/device adapter, device.py, runner.py, CLI composition, tests and CI workflow. Acceptance covers F10; recovery algorithm changes belong to T02.

### T02: fix recovery escalation and map idle rotation

Scope: make the public dispatcher allow the recovery timeout ladder while foreground remains FALSE; preserve the prohibition on screen gestures in a foreign app. Add idle_map_since/last_rotation_ts with the reset semantics above. Route rotation-dependent side effects conditionally, using a temporary receipt guard if T03 has not yet landed.

Acceptance:

1. Through `fsm.step`, exhausted foreground attempts plus expired RECOVERING time yield the permitted restart/halt escalation. No Tap/Swipe/Back/Pinch is emitted when foreground is FALSE or UNKNOWN; a positive game foreground observation is required before screen input resumes.
2. Through Runner, consecutive fresh confirmed-map frames with no targets eventually request rotation. Repeated map timestamps do not postpone it indefinitely.
3. Reject a rotation at admission: spatial memory and completed-rotation bookkeeping stay unchanged. Successful admission starts a bounded wait, rather than immediate repeated requests.
4. Acquiring a target, leaving map, capture reset and pause reset the idle interval. Recovery still uses last_map_ts and preserves existing switch/restart grace periods.
5. Use fake dual-domain time at threshold boundaries, including paused intervals and UNKNOWN-to-TRUE foreground recovery. Retain bounded attempt and terminal halt tests. Regressions fail on pre-fix code and pass after the narrow fixes.

Likely files: Context, Scanning, dispatcher, Runner application and focused public-path tests. Do not restructure all handlers.

### T03: make command completion and cancellation explicit

Scope: implement contracts and ActionPort, pure rendering, Dispatch, runner event folding and generation invalidation. Move the active Runner to receipts in one integrated patch; migrate every actuation-dependent sibling group. Split work into reviewable commits: types/fakes, worker/events, runner dependencies, cancellation/fault tests. Keep this one feature ticket because partial runtime migration would leave misleading counters and unsafe sibling effects.

Acceptance:

1. Force admission rejection by rate limit, full queue, breaker and closed port. Each produces a terminal rejection and cannot set intent, transition dependent state or count execution.
2. Block a fake transport after action A starts, queue B, invalidate by pause and release A. B never starts and receives CANCELLED. Repeat for halt, stale frame, capture epoch, unexpected episode and display changes. Close cancels B rather than draining it.
3. Race invalidate against worker start with explicit synchronization barriers. Assert both legal outcomes and honest in-flight uncertainty. Use no sleeps as ordering assertions.
4. Timeout/partial failure cannot count as EXECUTED or cause blind retry. Duplicate request/event delivery cannot duplicate execution or statistics; late feedback cannot advance a new generation.
5. Dry-run/replay emits SIMULATED, uses live-equivalent pacing and never uploads an injector or invokes subprocess. Backend setup failure is observable. Worker exceptions always finish the action and free pending state.
6. No frame-loop wait on transport completion. Pending queue/events remain bounded or have documented run-scoped retention; overflow fails visibly. Existing no-click, recovery, preflight and switching semantics have focused regression coverage.
7. ActionRequest retains the exact FrameRef and uses physical MonotonicSeconds for submission/expiry; ActionEvent uses that same identity and physical event time. Gameplay deadlines use ActiveSeconds only. Fake-clock tests prove pause/resume cannot make a fresh action expire or make frame age negative.

Likely files: contracts.py, effects.py, actions.py, Context, runner.py and fault tests. Lead owns shared edits. This ticket establishes the interfaces used by later specialists; T04 replaces persistence/reporting labels before autoplay ships.

### Later-ticket acceptance gates

* T04: persisted v1 examples survive round trip; repeated v2 events count once; failed/simulated throws never count as executed; unknown catches/rewards stay unknown; historical dashboards clearly mark legacy data. V2 timestamps use explicit at_mono_s and optional at_active_s; migration tests prove pause time is not charged to gameplay totals and event ordering uses physical monotonic time.
* T05: explicit class maps and one-to-one matching penalize duplicates, wrong classes and negatives. CPU evaluation is supported. Per-sample approvals bind image/label hashes; reject changed/unapproved data and recording/session split leakage across repeated promotions, including when an intermediate manifest omits a held-out recording. Require matching collision-checked image/label stems in exported YOLO pairs. Deliver a data-needed manifest where examples are missing.
* T06: planner validates finite coordinates, duration, ordering and usable bounds; fixed seed is reproducible. Fake backend verifies continuous contact and release cleanup. Real backend support remains unconfirmed until device tests record actual emitted input/timing. No automatic fallback that silently changes gesture meaning.
* T07: full stabilization warm-up, FrameRef/capture-epoch discontinuities and contradictory evidence are tested. Every runtime Evidence carries the capture-stamped FrameRef; old asynchronous results cannot be restamped into a new epoch. Evidence extraction refuses existing outputs, stages files in an exclusive per-extraction directory and never overwrites/deletes files it did not create. Held-out session-grouped outcome metrics include sample counts/confusion and missing classes. No new weights promoted using recall alone. No required outcome data means T08 is blocked from live release, even if synthetic tests pass.
* T08: phases READY -> AIMING -> ACTION_PENDING -> LAUNCH_PENDING -> RESULT_PENDING -> resolved/unknown, with explicit bounded failure exits. One outstanding throw per episode; EXECUTED without launch waits/revalidates. Breakout requires new readiness before retry. Out-of-balls needs direct evidence; unknown does not trigger restock. Annotated replay covers catch, flee, breakout, attack, no-balls and uncertainty.
* T09: confirmed disc visible -> supported spin -> await reward -> exit; visits never count as rewards. Assisted mode requires fresh capability evidence. Stop retries and restock are bounded, with quota reservations surviving uncertain results. Standard-client behavior reports missing features honestly.
* T10: public Runner/FSM fault scenarios cover stale frames, transport failure, cancellation, foreign/unknown foreground, exhausted restart, event replay and missing providers. Then supervised same-device sessions measure launches/requested throws, confirmed catches per active hour, balls per confirmed catch, false target taps, rewards, recovery time and interventions. Report uncertainty and test environment; no claimed improvement without measurements.

## Merge and release gates

1. Rebase on current target HEAD, inspect applicable AGENTS.md and preserve concurrent changes. One owner per shared file; specialist submits narrow diffs if their task overlaps the lead.
2. PR includes scope, changed public interfaces, before/after reproduction, exact checks/results, missing data and remaining phone validation. Tests exercise the public dispatcher/runner, not just private helpers.
3. Reviewer checks cancellation ordering, receipt/outcome distinctions, dry-run isolation and schema compatibility. Offline CI must pass; corpus failures must be classified explicitly and cannot be waived for a feature relying on that corpus.
4. Lead merges dependencies in order. Detector-only T05 can proceed after T01; T03 gates outcome evidence (T07) and gesture transport (T06). After T03, T05/T06 may proceed in parallel with disjoint ownership; T07 follows T03 and T05. Branches may prepare collection specs earlier but must not invent runtime interfaces.
5. No automated live release until T10's supervised gate. Keep existing assisted behavior available behind explicit capability checks during migration; select new controllers explicitly until their release evidence exists.

## First milestone and non-goals

First milestone: T01 and T02 merged with a reproducible hermetic baseline and both targeted regressions. Second milestone: T03/T04 provide truthful action lifecycle and cancellation before more autoplay features.

Non-goals: broad FSM rewrite; LLM decisions in the frame loop; model training before data/evaluation gates; account roster changes; independent GPS navigation; full combat; transfers, item deletion, purchases or external notifications; unattended live testing; claims of undetectability or lower ban risk. Curved throws are deferred until straight-throw calibration and continuous-pointer backend validation exist.
