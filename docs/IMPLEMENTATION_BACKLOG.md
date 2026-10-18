# PoGoBot shared interfaces and ordered implementation backlog

Status: proposed integration contract v1, 17 October 2026. Documentation only; the APIs below are not implemented yet.

Repository: https://github.com/zidanerick/PoGoBot

Inspected HEAD: `9795280ec52b67cde316c45aa531e64c39ddd9b8`. This follows the upstream audit baseline `78149d3ae124e84c604e99ac96ef91a193111de2`. Read `docs/AUTOPLAY_PROJECT_PLAN.md` for the audit, evidence limitations and role prompts. Recheck HEAD before implementation and preserve newer changes.

## Architecture decision and boundaries

Keep capture -> Observation -> pure FSM -> Effects -> runner -> serialized actuator. Workers publish data; only the runner mutates Context, statistics and ledgers. No LLM enters the real-time loop. Add adapters and focused contracts before extracting large modules.

Source inspected for this plan: `frames.py`, `observation.py`, `effects.py`, `actions.py`, `device.py`, `perception.py`, `fsm.py` and `runner.py`. The source still contains the foreground early return, map-idle clock coupling, acceptance-based throw accounting and queue-draining shutdown identified by the audit. The audit's test results are historical evidence, not a fresh test run for this document. No phone behavior or new accuracy result has been measured.

### Ownership

| Role | Owns implementation | Shared-file rule |
|---|---|---|
| Lead/integrator | `contracts.py`, `effects.py`, `observation.py` public fields, `Context` fields, `runner.py`, config/CLI wiring and migrations | Sole author of shared integration edits; incorporates narrow patches supplied by specialists |
| Reliability | `actions.py`, device adapter, recovery and idle-rotation fixes | Supplies regression and handler patches to lead for shared files |
| Perception/evaluation | `perception.py`, evidence producers, annotation/evaluation tools | Uses approved observation additions; never edits gameplay policy |
| Gesture/calibration | New `gestures.py`, backend implementation and calibration tool | Consumes approved trajectory and action contracts; coordinates actuator changes with reliability |
| Autoplay | New encounter/stop policy helpers, handler patches | Consumes receipts/evidence; lead wires into FSM/runner |
| Reviewer | Offline fault/replay harness and independent review | Reports findings; does not silently modify another owner's patch |

These are assignments, not instructions to launch agents. Start lead and reliability work first. Later allow at most two implementation branches at once with disjoint ownership. Shared interface changes require a contract revision in this file before dependent branches change them.

## Contract v1

Types live in proposed `pogobot/contracts.py` unless specified otherwise. Frozen dataclasses and string enums are preferred. The following signatures and semantics are the target public API. Existing public constructors remain valid through defaulted optional additions.

### 1. Time, identity and device observations

```python
class Clock(Protocol):
    def now(self) -> float: ...

@dataclass(frozen=True)
class DeviceSnapshot:
    sampled_at: float
    foreground: Tristate = Tristate.UNKNOWN
    keyboard: Tristate = Tristate.UNKNOWN
    online: Tristate = Tristate.UNKNOWN
    logical_wh: tuple[int, int] | None = None

class DeviceServices(Protocol):
    def snapshot(self, now: float) -> DeviceSnapshot: ...
    def close(self) -> None: ...
```

`Clock.now()` uses the same monotonic epoch as `Frame.ts`, `Observation.ts`, Context time, deadlines and action events. `Frame.seq` is strictly increasing within a capture epoch; `Observation.seq/ts` identify the originating frame, not inference completion. Sequence identity is `(run_id, capture_epoch, seq)`. Increment capture epoch on reconnect/replay reset, reset temporal perception history and invalidate old actions. Never compare wall time with monotonic time. Persist relative timestamps plus a wall-time run anchor for display; monotonic deadlines do not survive process restart.

Live device snapshots are cached and nonblocking. On timeout, parse failure or expiry, affected facts are UNKNOWN, not FALSE. A positively observed other foreground is FALSE. Replay supplies snapshots explicitly and must never construct live pollers, resolve display size through ADB, initialize an injector, start an app or query device state. Unknown/stale foreground blocks new screen input until refreshed; bounded recovery can still progress to a logged halt. Foreground/restart commands require a separate bounded recovery policy and must not bypass cancellation checks accidentally.

Runner gains trailing keyword-only `clock: Clock | None = None` and `device_services: DeviceServices | None = None`. CLI composition selects live or fake dependencies explicitly. Unit tests use fake services; the offline harness rejects unprovided live dependencies. Inject timing into frame sources, actuator and pollers as needed rather than changing only Runner's clock.

Keep `last_map_ts` for last confirmed map visibility. Add `idle_map_since: float | None` and `last_rotation_ts: float | None` to Context. Start the idle interval on entry to a confirmed map with no actionable target. Clear it when a target is acquired, map visibility is lost, capture epoch changes or pause occurs. Reset it on an accepted rotation; rejection must not clear spatial memory or mark rotation complete. Repeated map frames do not reset it. A later execution failure triggers recovery/revalidation rather than pretending the camera moved. No rename of `last_map_ts` in the first patch.

### 2. Action requests, receipts and feedback

```python
@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    generation: int
    capture_epoch: int
    based_on_seq: int
    based_on_ts: float
    submitted_at: float
    expires_at: float
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
    at: float
    reason: str | None = None
    execution_uncertain: bool = False

class ActionPort(Protocol):
    def submit(self, request: ActionRequest) -> Submission: ...
    def poll_events(self) -> tuple[ActionEvent, ...]: ...
    def invalidate(self, generation: int, reason: str) -> None: ...
    def close(self) -> None: ...
```

Runner creates deterministic unique IDs `run_id:action_counter` before submission, records PROPOSED and reserves a pending slot. The worker may publish immediately, but only the runner consumes events on a later tick. One accepted gameplay action may be outstanding per episode. Budgets continue to control pacing; purpose is explicit and is not inferred from human-readable `reason`. Recovery commands have explicit purposes too.

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
    kind: str
    value: Tristate
    confidence: float | None
    seq: int
    ts: float
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

Add `evidence: tuple[Evidence, ...] = ()` to Observation, leaving existing constructors and signals intact. Fixed initial evidence names: `ball_ready`, `ball_launched`, `ball_in_flight`, `target_attacking`, `catch_confirmed`, `flee_confirmed`, `breakout_confirmed`, `out_of_balls`, `stop_disc_visible`, `stop_reward_visible`. Missing evidence means UNKNOWN, never false. `confidence=None` means no probabilistic score, not zero confidence. Include source/model/version in the trace manifest. All regions use normalized stream coordinates; raw optical fractions remain available in existing Signal fields. Add `aim_region` as evidence only when actually measured and validated; do not repurpose a generic detection box as a calibrated throw target.

Perception emits facts without episode IDs or gameplay policy. Runner/controller associates temporally relevant evidence with the active episode. A GameOutcome requires post-action or explicit independent outcome evidence in the current capture epoch. A map return alone cannot distinguish catch, flee and user exit. An executed swipe alone cannot prove ball launch. Unknown outcomes resolve a bounded episode without incrementing catch/reward totals. Outcome IDs deduplicate persistent stats and quota updates. Observed positive and inferred results are counted separately. Conflicting positives remain unknown pending bounded re-observation. Stabilization counts distinct sequences, requires its full warm-up vote and resets on discontinuity. Evidence thresholds and temporal windows must be selected from data, not invented in this contract.

### 5. Provider and gesture interfaces

```python
@dataclass(frozen=True)
class Capability:
    name: str
    availability: str  # supported, unsupported, unknown
    enabled: Tristate
    checked_at: float
    reason: str

class Provider(Protocol):
    def capabilities(self, now: float) -> tuple[Capability, ...]: ...

@dataclass(frozen=True)
class TouchPoint:
    t_ms: int
    x: float
    y: float

@dataclass(frozen=True)
class TouchTrajectory:
    points: tuple[TouchPoint, ...]
    profile_id: str
    seed: int
```

Initial provider capabilities: `auto_spin`, `auto_battle`, `autowalk`. Distinguish provider support from enabled state; require fresh positive support and enabled evidence before relying on assisted behavior. Unknown blocks that dependent feature and produces a reason. A standard-client adapter declares unsupported provider features and can still use implemented manual touch actions. Capability snapshots are read-only inputs in Context; providers never actuate from `capabilities()`.

TouchTrajectory lives in contracts.py. New `TraceGesture(trajectory, reason, budget="throw")` lives in effects.py; add it to ACTUATIONS only when a backend implements it. Coordinates are finite normalized fractions in [0,1], origin top-left. Points require at least two entries, first t_ms=0, subsequent times strictly increase, and a validated maximum duration/sample count. Backend emits one continuous down/move/up contact, releases on normal completion, and attempts release in failure/cancellation cleanup. No sequence of independent swipes qualifies. Convert to logical device pixels once in the backend, using a validated viewport/orientation profile; invalidate the profile on geometry change. Unsupported trajectories return an explicit rejection; straight Swipe remains available where the caller explicitly selects a supported fallback.

Pure planner API in gestures.py: `plan_straight(start, end, duration_ms, profile_id, seed) -> TouchTrajectory`. Calibration profiles record device/viewport identity, backend/version, latency observations, usable origin/target areas and validation date. Seeded variation must stay within the measured usable area and never compensate for uncertain perception. Keep queue delay, transport delay and on-device gesture duration separate in logs. Curve planning and performance claims wait for backend validation. Smooth motion is a quality objective; it provides no basis for a reduced-ban-risk claim.

### 6. Event schema and migration

Emit versioned event envelopes: `schema_version=2`, `event_id`, `run_id`, `capture_epoch`, monotonic-relative `at`, `type`, optional `action_id`, `episode_id`, `generation`, and typed `payload`. Distinguish action events from game outcomes. Include run configuration/model hashes and timestamp anchor once in a run manifest. Do not log credentials, raw account names or notification secrets.

New counters: `throws_proposed`, `throws_accepted`, `throws_executed`, `ball_launches_observed`, `catches_observed`, `catch_outcomes_unknown`, `stop_spins_executed`, `stop_rewards_observed`, plus failure/cancellation and simulation counters. Deduplicate launches/rewards across repeated frames, not just command completions. Ratios state their numerator, denominator and sample counts.

Preserve old saved files; read missing schema_version as v1 and retain legacy counters under clearly marked legacy/inferred fields. Never reinterpret historical balls-thrown values as executed throws, or historical stop visits as rewards. V2 writes are additive initially; deprecate ambiguous display labels when consumers migrate. Keep local configured quota conservative by reserving accepted stop attempts and recording confirmed rewards separately; an unknown result must not free a reservation merely because no reward was seen. Simulated/inferred events do not increment confirmed totals. Prove migration with representative saved records before changing stats/quota writers.

## Ordered tickets

Dependencies are merge prerequisites. Each ticket is a separate bounded PR with acceptance tests written before its fix. No agent should start implementation against an unmerged interface draft.

| ID | Owner | Deliverable | Depends on |
|---|---|---|---|
| T01 | Lead + reliability | Hermetic runtime seam and baseline CI | This contract review |
| T02 | Reliability, lead integrates | Foreground escalation and idle rotation fixes, F1/F2 | T01 |
| T03 | Reliability, lead integrates | Action lifecycle, cancellation and conditional effects, F3/F4 | T01, T02 |
| T04 | Lead | V2 event accounting, legacy migration and consumers | T03 |
| T05 | Perception/evaluation | Reviewed data gate, class-aware matcher and outcome collection spec, F8/F9 | T01; approved Evidence types from T03 |
| T06 | Gesture + reliability | Pure planner, backend feasibility and device profile | T03 |
| T07 | Perception, lead integrates | Outcome evidence and stable temporal perception, F5/F7/F11 | T05 |
| T08 | Autoplay, lead integrates | Provider capabilities and closed-loop encounter | T03, T04, T06, T07 |
| T09 | Autoplay, lead integrates | Confirmed stop loop and bounded restock integration | T08 |
| T10 | Reviewer + lead | Fault/replay milestone review and supervised comparison | T09 |

### T01: establish a hermetic baseline

Scope: add Clock/DeviceServices seams, live cached foreground polling and deterministic fake snapshots. Route runtime foreground, online, size and keyboard reads through composition; ensure replay startup and teardown avoid live services. Add a fast offline CI job and separately classified vision/corpus checks. Do not upgrade or lock all dependencies as an incidental change.

Acceptance:

1. Construct/run/close Runner with fake source, perceptor, action port and device services while subprocess and network access are blocked. Exercise pause/resume and replay with zero external calls.
2. Foreground timeout returns UNKNOWN; confirmed other app returns FALSE; stale snapshots expire. Frame loop does not wait on the slow query. Unknown does not authorize gameplay input.
3. Replay time deterministically advances all deadlines. Repeating the same scenario produces identical decisions/events.
4. Run `python -m pytest tests -q` and report exact failures/skips. Missing packages/corpora are explicit capabilities in split jobs, never hidden blanket skips. Record the new baseline rather than copying the audit's counts.

Likely files: new contracts/device adapter, device.py, runner.py, CLI composition, tests and CI workflow. Acceptance covers F10; recovery algorithm changes belong to T02.

### T02: fix recovery escalation and map idle rotation

Scope: make the public dispatcher allow the recovery timeout ladder while foreground remains FALSE; preserve the prohibition on screen gestures in a foreign app. Add idle_map_since/last_rotation_ts with the reset semantics above. Route rotation-dependent side effects conditionally, using a temporary receipt guard if T03 has not yet landed.

Acceptance:

1. Through `fsm.step`, exhausted foreground attempts plus expired RECOVERING time yield the permitted restart/halt escalation. No Tap/Swipe/Back/Pinch is emitted against foreign foreground.
2. Through Runner, consecutive fresh confirmed-map frames with no targets eventually request rotation. Repeated map timestamps do not postpone it indefinitely.
3. Reject a rotation at admission: spatial memory and completed-rotation bookkeeping stay unchanged. Successful admission starts a bounded wait, rather than immediate repeated requests.
4. Acquiring a target, leaving map, capture reset and pause reset the idle interval. Recovery still uses last_map_ts and preserves existing switch/restart grace periods.
5. Use fake time at threshold boundaries; retain bounded attempt and terminal halt tests. Regressions fail on pre-fix code and pass after the narrow fixes.

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

Likely files: contracts.py, effects.py, actions.py, Context, runner.py and fault tests. Lead owns shared edits. This ticket establishes the interfaces used by later specialists; T04 replaces persistence/reporting labels before autoplay ships.

### Later-ticket acceptance gates

* T04: persisted v1 examples survive round trip; repeated v2 events count once; failed/simulated throws never count as executed; unknown catches/rewards stay unknown; historical dashboards clearly mark legacy data.
* T05: explicit class maps and one-to-one matching penalize duplicates, wrong classes and negatives. CPU evaluation is supported. Per-sample approvals bind image/label hashes; reject changed/unapproved data and recording/session split leakage. Deliver a data-needed manifest where examples are missing.
* T06: planner validates finite coordinates, duration, ordering and usable bounds; fixed seed is reproducible. Fake backend verifies continuous contact and release cleanup. Real backend support remains unconfirmed until device tests record actual emitted input/timing. No automatic fallback that silently changes gesture meaning.
* T07: full stabilization warm-up, sequence discontinuities and contradictory evidence are tested. Held-out session-grouped outcome metrics include sample counts/confusion and missing classes. No new weights promoted using recall alone. No required outcome data means T08 is blocked from live release, even if synthetic tests pass.
* T08: phases READY -> AIMING -> ACTION_PENDING -> LAUNCH_PENDING -> RESULT_PENDING -> resolved/unknown, with explicit bounded failure exits. One outstanding throw per episode; EXECUTED without launch waits/revalidates. Breakout requires new readiness before retry. Out-of-balls needs direct evidence; unknown does not trigger restock. Annotated replay covers catch, flee, breakout, attack, no-balls and uncertainty.
* T09: confirmed disc visible -> supported spin -> await reward -> exit; visits never count as rewards. Assisted mode requires fresh capability evidence. Stop retries and restock are bounded, with quota reservations surviving uncertain results. Standard-client behavior reports missing features honestly.
* T10: public Runner/FSM fault scenarios cover stale frames, transport failure, cancellation, foreign/unknown foreground, exhausted restart, event replay and missing providers. Then supervised same-device sessions measure launches/requested throws, confirmed catches per active hour, balls per confirmed catch, false target taps, rewards, recovery time and interventions. Report uncertainty and test environment; no claimed improvement without measurements.

## Merge and release gates

1. Rebase on current target HEAD, inspect applicable AGENTS.md and preserve concurrent changes. One owner per shared file; specialist submits narrow diffs if their task overlaps the lead.
2. PR includes scope, changed public interfaces, before/after reproduction, exact checks/results, missing data and remaining phone validation. Tests exercise the public dispatcher/runner, not just private helpers.
3. Reviewer checks cancellation ordering, receipt/outcome distinctions, dry-run isolation and schema compatibility. Offline CI must pass; corpus failures must be classified explicitly and cannot be waived for a feature relying on that corpus.
4. Lead merges dependencies in order. At most T05 and T06 proceed in parallel after T03. T07 can follow T05 while gesture work finishes. Branches may prepare collection specs earlier but must not invent runtime interfaces.
5. No automated live release until T10's supervised gate. Keep existing assisted behavior available behind explicit capability checks during migration; select new controllers explicitly until their release evidence exists.

## First milestone and non-goals

First milestone: T01 and T02 merged with a reproducible hermetic baseline and both targeted regressions. Second milestone: T03/T04 provide truthful action lifecycle and cancellation before more autoplay features.

Non-goals: broad FSM rewrite; LLM decisions in the frame loop; model training before data/evaluation gates; account roster changes; independent GPS navigation; full combat; transfers, item deletion, purchases or external notifications; unattended live testing; claims of undetectability or lower ban risk. Curved throws are deferred until straight-throw calibration and continuous-pointer backend validation exist.
