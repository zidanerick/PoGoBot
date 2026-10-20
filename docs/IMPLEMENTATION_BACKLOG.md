# PoGoBot shared interfaces and ordered implementation backlog

Status: proposed integration contract v1.1, 21 October 2026. This revision resolves independent-review gaps in clock domains and capture identity. APIs remain proposals until implemented and accepted.

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
| Gesture/calibration | `gestures.py`, gesture backend, Java/Dex build and calibration tooling | Consumes the frozen trajectory, clock and ActionPort contracts. Does not edit `actions.py` or Runner; supplies an adapter patch for reliability/lead to integrate. |
| Autoplay | New encounter/stop policy helpers and focused deterministic replay tests | Does not edit public Context, FSM dispatcher, Runner, contracts or event schema directly. Supplies narrow handler/policy patches to the lead for integration after the interface gate. |
| Reviewer | Offline fault/replay harness and independent review | Reports reproducible findings and test requirements; does not edit another owner's implementation. |

These are ownership boundaries, not instructions to launch agents. One owner edits each shared file at a time. Specialists keep changes in their owned files and submit narrow patches when integration surfaces are needed; the lead applies those patches on the integration branch. No parallel branch may redefine a public clock, identity, receipt or evidence field.

## Contract v1.1

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

The heads rechecked were #1 contract v1.1 at `ab5ed265e3040cc7713d86cde56aeb34b88270d4`, #2 at `a7fb3b5adea9f7619f6e9380fa1b1935ea1e410a`, #3 at `8a332eeb9453f5fb285af1e11c5bda9f850945b7`, and #4 at `404e171fbf86a651f16b339e5669360653ab9b21`. This plan update advances only PR #1; the final head is recorded in the commit history. All target main at `9795280ec52b67cde316c45aa531e64c39ddd9b8`; no branch is merged. Recheck gates if any head changes.

| PR | Scope and dependency | Exact merge acceptance | Required tests/evidence |
|---|---|---|---|
| #1 Shared contract | No code dependency; contract review is prerequisite to every implementation PR. | This PR updates v1.1 with the two time domains, immutable FrameRef propagation, unknown-input rule, cross-PR branch order and criteria below. The contract text addresses the independent findings on clock domains and frame identity; GitHub review-thread status remains unchanged by this documentation update. | Documentation diff is readable Markdown; `git diff --check`; review every API field for a named time domain; verify old constructors remain explicitly transitional only. No runtime behavior claim or runtime test is expected from this docs-only PR. |
| #3 Runtime reliability | Depends on accepted #1 contract. Implements parts of T01/T02, not the dual-clock/FrameRef baseline. | Injected dual-domain Clock and end-to-end FrameRef remain prerequisites. Existing async home navigation must remain nonblocking. It is a release blocker if a fresh post-teleport reader result is unavailable, stale, or times out: report UNKNOWN/failure through the notifier, never mark navigation successful, and never tap guessed coordinates or retry blindly. Preserve bounded recovery, foreground barriers and accepted-rotation-only cooldown clearing; replay performs no device I/O. | On the exact head, run focused Runtime/Runner/FSM/device/account tests and `python -m pytest tests --ignore=tests/test_sponsored_ad.py -q`, reporting exact skips/failures. Add a controlled fake-reader test for account switch with home_favorite: successful fresh post-action tree, unavailable initial tree, unavailable post-teleport tree, stale result after invalidation, timeout, and notifier result. Assert unavailable/stale/timeout gives no success result or success log, calls notifier once, and issues no coordinate guess or repeat teleport. Also test foreground UNKNOWN across every public screen-input path, recovery escalation, accepted/rejected rotation, nonblocking workers, and frame-reset/delayed-result once FrameRef is implemented. No live-device run is required for merge. |
| #2 Gesture/calibration | Planner contract depends on #1. Transport integration depends on T03 ActionPort; keep gameplay disconnected until then. | Separate pure planning/calibration from transport if needed. Planner validates bounds, duration, ordering, seeded repeatability and geometry. Transport consumes the T03 ActionRequest/cancellation generation, uses the shared physical Clock, releases pointer on every exit, and reports uncertainty honestly. Dex includes every nested Java class. No fallback silently changes trajectory semantics. | Planner/property tests; fake PointerSink tests for down/move/release, cancellation and release failure; Java compilation plus D8 packaging test that inspects the dex for Gesture and nested Point/MotionAction classes; action-adapter tests for stale epoch/generation and expiry. Report that device timing remains unverified until a supervised device run. |
| #4 Perception/evaluation | Split before merge. T05 detector evaluation/promotion depends on #1 and T01. T07 outcome evidence depends on #1, T03 identity/evidence types and T05 schema. | At #4 head `404e171`, README now fetches as UTF-8 readable Markdown, and the prior source-level split-assignment, image/label-stem and evidence-preservation defects have regression tests. These are not accepted until the focused suite passes on this exact head. T07 remains offline evidence evaluation/extraction, not runtime FrameRef propagation. Keep no-accuracy claim while no reviewed corpus exists. | Run `python -m pytest tests/test_evaluation.py -q` on exact head; require README decode/render test, two-run split assignment preservation with omitted recordings and conflicts, mismatched-stem rejection before writes, collision/rerun byte-preservation, and partial-extraction rollback preserving pre-existing frames/manifests. Record exact count. No detector or game accuracy claim without a reviewed corpus. |

Review status at these heads: #1's clock-domain and FrameRef findings are addressed in the proposed document, but remain proposals and the GitHub threads are not resolved. #2's current source/body includes all Java class inputs and a DEX parser check; its prior review thread is outdated, but actual D8 output was not built because D8 is unavailable. #3's earlier UNKNOWN-input and rejected-rotation findings have source fixes and regression coverage reported on `0054eb2`, while the current `8a332ee` head adds async home navigation. That fixes the original missing-read incompatibility. However, `AsyncHomeNavigator._consume()` currently marks the destination successful when the post-jump `AccountView` is unavailable; `test_async_home_navigation_failure_reaches_notifier` covers only an unavailable initial view. This post-action UNKNOWN case is an explicit release blocker. The 87 focused and 990 passed/39 skipped results are for the prior `0054eb2` head, not current `8a332ee`. #4's `404e171` README fetches as UTF-8 and source/tests cover the earlier split/stem/extraction findings, but no passing `tests/test_evaluation.py` result for `404e171` is present. Re-run exact-head tests; a green earlier head or test source alone is not acceptance.

### Proposed branch order

This is a dependency order, not an instruction to merge now. Keep PR #1 as the proposed contract source. Before implementation branches consume it, reviewers and owners must accept the API and semantics in the interface-freeze gate below.

1. **#1 contract v1.1, updated on top of `5d92fc5` to `3500c8b`**: approve the shared API and this ownership/order plan. No runtime code is included.
2. **#3 Runtime at `8a332ee`**: rebase/continue from the accepted #1 contract. It supplies partial T01/T02 work. It does not pass integration until the post-teleport UNKNOWN case fails closed and current-head tests pass.
3. **T03 ActionPort PR, not open**: integrate the request/receipt lifecycle, cancellation generations, Dispatch, and runner feedback. #2 transport and #4 T07 outcome linkage wait for this.
4. **T04 event migration PR, not open**: migrate persisted events and accounting on top of T03. T08 waits for this.
5. **Parallel after T03, with disjoint ownership**:
   - **#2 Gesture at `a7fb3b5` / T06**: pure planner can be reviewed against #1 independently; transport adapter waits for T03 and must use its ActionPort, FrameRef and cancellation generation.
   - **#4 Perception at `404e171` / T05 + T07**: split before merge. T05 detector/evaluation data integrity depends on #1 and T01; it can be prepared after #1 and merges after T01. T07 runtime-linked outcome evidence depends on T03 and T05.
6. **T08 Autoplay PR, not open**: only after T03, T04, T06 and T07 have passed their gates and the interface-freeze gate is accepted.
7. **T09 stop/restock PR, not open**: after T08.
8. **T10 fault/replay and supervised milestone**: after T09. No automated live release before this gate.

No Autoplay PR is open at these heads. Autoplay may develop isolated policy tests against frozen interfaces, but its implementation branch must not redefine clock, frame identity, action receipts, evidence, or event schemas.

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

### Contract field status at recheck

| Contract area | Status at current PR heads |
|---|---|
| MonotonicSeconds, ActiveSeconds, Clock, FrameRef, DeviceServices protocol and capture-to-evidence propagation | Proposed in #1 v1.1. #3 has device pollers and pause-adjusted legacy FSM time, but no shared dual-domain Clock or FrameRef propagation. |
| ActionRequest, Submission, ActionEvent, ActionPort, Dispatch, action generations and cancellation lifecycle | Proposed in #1. #3 still uses acceptance-style apply(effect)->bool; #2 has an isolated gesture transport/receipt path, not the shared ActionPort. |
| Evidence.frame_ref, GameOutcome runtime association, episode-safe outcome decisions and v2 event timestamp/accounting schema | Proposed in #1. #4 provides offline reviewed manifests and scoring/extraction, not runtime frame identity or GameOutcome integration. |
| Trajectory planner, calibration, Java injector, isolated transport and DEX class inspection | Implemented in #2 source/tests as an isolated feature. It is not connected to Runner/ActionPort, does not consume the shared FrameRef/Clock contract, and actual D8 output/device input remain unverified. |
| Nonblocking foreground/UI-tree polling, UNKNOWN tri-state, bounded recovery, rotation acceptance guard, async home-navigation state machine | Implemented in #3 source. The post-teleport UNKNOWN branch currently reports success incorrectly; this behavior is not release-accepted. |
| Detector/outcome evaluation tools, reviewed promotion and extraction safeguards, readable README | Implemented in #4 source/tests at a source-inspection level. README is readable UTF-8 at 404e171; exact-head test execution remains missing. No model/corpus accuracy has been measured. |
| Provider capability API/consumer gates and Autoplay encounter/stop loops | Proposed in #1; no current Autoplay implementation PR exists. |

## Ordered tickets

Dependencies are merge prerequisites. Each ticket is a separate bounded PR with acceptance tests written before its fix. No agent should start implementation against an unmerged interface draft.

| ID | Owner | Deliverable | Depends on |
|---|---|---|---|
| T01 | Lead + reliability | Hermetic runtime seam, dual-domain clock, capture identity and baseline CI | Contract v1.1 review |
| T02 | Reliability, lead integrates | Foreground escalation and idle rotation fixes, F1/F2 | T01 |
| T03 | Reliability, lead integrates | Action lifecycle, cancellation and conditional effects, F3/F4 | T01, T02 |
| T04 | Lead | V2 event accounting, legacy migration and consumers | T03 |
| T05 | Perception/evaluation | Reviewed detector data gate, class-aware matcher and collection spec, F8/F9 | Contract v1.1; T01 |
| T06 | Gesture + reliability | Pure planner, backend feasibility and device profile | Contract v1.1; T03 for transport |
| T07 | Perception, lead integrates | Runtime-linked outcome evidence and stable temporal perception, F5/F7/F11 | T03; T05 |
| T08 | Autoplay, lead integrates | Provider capabilities and closed-loop encounter | T03, T04, T06, T07 |
| T09 | Autoplay, lead integrates | Confirmed stop loop and bounded restock integration | T08 |
| T10 | Reviewer + lead | Fault/replay milestone review and supervised comparison | T09 |

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
