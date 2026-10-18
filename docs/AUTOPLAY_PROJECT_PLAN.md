# PoGoBot autoplay audit and agent starter pack

Reviewed 17 October 2026. Repository: https://github.com/zidanerick/PoGoBot

Source baseline: `78149d3ae124e84c604e99ac96ef91a193111de2` from `1tzkaos/PoGoBot`.

## Recommendation

Keep the current capture -> perception -> pure state machine -> effects -> actuator architecture. Build observable, bounded autoplay on it. Do not begin with a rewrite, more account-switching features, or random delays. First fix recovery and action accounting; then add outcome perception, calibrated gestures, and complete encounter/stop loops.

Use six engineering roles, normally only two implementation agents concurrently plus a reviewer when needed. These are development agents, not GPT calls inside the frame-processing loop. Keep runtime vision and control local and deterministic. Natural-looking touch trajectories are a usability and motion-quality objective, not evidence of undetectability or reduced account-ban risk.

This is a repository-wide architecture and targeted defect audit, not an exhaustive proof of every line or a live-device certification. No phone session, new model training, or measured catch-rate comparison was performed. Source comments containing historical measurements were not independently reproduced.

## Repository map

| Area | Files | Assessment |
|---|---|---|
| Entry/configuration | `cli.py`, `config.py`, `userconfig.py`, `__main__.py` | Extensive controls and per-account settings. Preserve CLI precedence; validate capability prerequisites explicitly. |
| Capture | `capture.py`, `frames.py` | Latest-frame delivery, age/health guards and replay are useful foundations. Live capture uses a POSIX FIFO. Replay needs recorded timing and fake device services for reproducible benchmarks. |
| Perception | `perception.py`, `observation.py`, bundled detector/classifier | Combines optical signals and YOLO. Normalized coordinates and explicit unknown states are valuable. Missing outcome and aiming signals are the principal accuracy limitation. |
| Decisions | `fsm.py` | Pure handlers are testable. Large switching/preflight subsystem shares a file with gameplay. Fix targeted problems before extracting modules. |
| Integration | `runner.py` | Applies effects, counts events, runs recovery and tracing. Main integration bottleneck and highest merge-conflict risk. |
| Device/input | `actions.py`, `effects.py`, `device.py`, `tools/pinch/Pinch.java` | Serialized input, rate limits and a failure breaker exist. Needs command identity, completion receipts, cancellation and trajectory support. Existing pinch injector is a reference, not proof that a curved single-pointer gesture backend works. |
| PGSharp integration | `accounts.py`, switching/preflight handlers | Reads overlay views, not the Unity game scene. AutoWalk/account operations are provider-specific. Make provider capabilities explicit. |
| Data/model tools | `learning.py`, `tools/promote_reviewed.py`, `tools/adopt_best_detector.py` | Review queue and deduplication are useful. Promotion does not enforce per-sample review approval; detector selection is too weak for action safety. |
| Reporting | `stats.py`, `quota.py`, `hud.py`, `tui.py`, `notify.py` | Keep one event source for all views. Some current labels imply outcomes that are only inferred. Notification delivery should stay asynchronous and opt-in. |
| Tests/packaging | `tests/`, `requirements.txt` | Broad tests, but real-device leakage and optional-dependency gaps exist. Requirements have lower bounds rather than a tested lock. No tracked GitHub Actions workflow was found at this baseline. |
| Historical code | `legacy/` | Reference only. Do not revive the old feedback-training path or import legacy controllers into runtime. |

## Findings, ordered by development impact

### F1: foreground recovery cannot reach its escalation timeout

**Reproduced.** `fsm.step` returns an empty list whenever foreground is false and state is already RECOVERING, before handler timeout dispatch. With time=500, state_since=0, last_map_ts=0 and foreground attempts exhausted, the dispatcher returned `[]`; directly calling the same recovery handler's timeout returned a RestartApp effect. This blocks the intended restart path. It does not prove the whole process runs forever, because runner watchdogs can still stop it.

Fix: keep refusing screen gestures in another app, while allowing bounded restart/halt escalation. Test through the public dispatcher, not only the handler.

### F2: idle map rotation uses the wrong clock

**Source-confirmed and exercised with a synthetic observation.** `Scanning.step` requires `now - last_map_ts >= scanning_idle_rotate`; `Runner.run` refreshes last_map_ts on every confirmed map frame before dispatch. Idle confirmed-map frames therefore never accumulate the required interval.

Fix: separate last-seen-map time from last-target/progress/rotation time. Preserve the map timestamp for recovery. Test repeated map-only frames through runner integration.

### F3: accepted command is not executed command or game outcome

**Source-confirmed.** `Actuator.apply` returns success when queued (also in dry-run); the worker executes later. `Runner.apply` increments throw counters at acceptance. SetIntent and Transition effects also run independently of whether an intervening tap was accepted. A rejected tap can therefore still advance decision state.

Fix: stable action IDs, explicit proposed/accepted/executed/failed/cancelled events and separately observed game outcomes. Make dependent effects conditional on acceptance or a transaction result. Keep dry-run simulated events distinct. Do not block the frame loop waiting for ADB.

### F4: pause does not cancel already queued gestures

**Source-level risk requiring a controlled worker test.** `_sync_pause` stops new decisions but does not invalidate the actuator queue; the worker consumes queued commands without a pause/state-generation check. `close` drains the queue. Thus already accepted actions may execute after the user pauses or the screen changes.

Fix: cancellation generations, expiry and an execution-time validity check. Define what happens to an already executing gesture, including pointer-up cleanup. Prove queued stale commands are discarded with a blocked fake transport.

### F5: encounter control is an open-loop fixed swipe

**Source-confirmed.** `Encounter.step` sends `(0.50, 0.84) -> (0.50, 0.38)` for 160ms. It does not aim at a measured Pokémon target, confirm ball availability/launch, detect attacks or wait on a classified result. The throw limit counts accepted requests.

Fix: explicit encounter phases: ready, aiming, gesture pending, ball in flight, result pending, confirmed catch/flee/breakout/unknown. Begin with accurate straight throws; add curved trajectories only after capture/transport calibration and outcome measurement.

### F6: stops and Rocket battles depend on external automation

**Source-confirmed.** Pokestop deliberately does not spin the disc; it opens, dwells and exits assuming auto-spin. Rocket advances dialogs and relies on an auto-battler. AutoWalk drives PGSharp UI. This is not a complete independent autoplay implementation.

Fix: capability adapter with supported/unsupported/unknown state. Separate existing provider-assisted operation from a standard-client touch-control mode. Implement confirmed disc spinning before independent combat. Defer walking/navigation and combat until the core loop is measured; touch motion does not supply GPS navigation.

### F7: reported success and quota accounting need evidence levels

**Source-confirmed.** Confirmed catches are not currently observable. Stops are counted from a stop-screen transition and that inferred event feeds quota accounting. A screen opening is not evidence of items awarded. Repeated unproductive throws can also be interpreted as needing restock without direct inventory evidence.

Fix: distinguish attempts, observed outcomes and inferred outcomes. Preserve historical schemas with migration/versioning. Show unknown where evidence is absent. Treat configured limits as local policy, not independently verified server limits.

### F8: model selection ignores false positives and class errors

**Source-confirmed.** `adopt_best_detector.score` discards ground-truth class IDs, checks whether any prediction overlaps each label, and ranks localization recall. It does not penalize false positives, enforce one-to-one matches, or score correct classes. It hardcodes `device="mps"`. The referenced `datasets/det_v3/valid` is not tracked in this checkout.

Fix: explicit class mappings, one-to-one matching, per-class precision/recall, false taps on negatives, latency, and screen-confusion metrics. Accept a device argument with CPU support. Keep a development validation split separate from an untouched final test split, grouped by recording/session. Do not claim the README's historical metrics were remeasured.

### F9: dataset review is a convention rather than an enforced gate

**Source-confirmed.** `promote_reviewed.py --promote --yes` moves every image/label pair returned by `pairs()`, without checking an individual approved-review record. Detection completeness is not guaranteed merely by retaining all model predictions.

Fix: require per-sample reviewer approval tied to image/label hashes, validate class IDs and bounds, reject unapproved samples, prevent split overlap, and retain provenance. Automated predictions may assist annotation, but are not ground truth.

### F10: replay and tests still reach live foreground queries

**Reproduced in tests; source-confirmed for the shared runner path.** A pause test failed after a real `device.app_foreground` query classified the absent device as another app. The same isolated test passed when that function was mocked to True. `_refresh_foreground` is not gated out in replay. Device query failures currently collapse to False instead of Unknown and can block the main loop up to the subprocess timeout.

Fix: injectable device service and clock; replay must make zero ADB calls. Distinguish query failure from positively observed foreign foreground. Move slow polling off the frame loop. Test absent-device and timeout cases.

### F11: startup stabilization and portability claims need tightening

**Source-confirmed.** ScreenStabilizer accepts a first label immediately via `min(needed, len(history))`; a one-frame synthetic encounter label was returned as stable. This is narrower than its comment promising no single-frame transitions. Other optical gates may mitigate it. Live capture calls `os.mkfifo` without a Windows alternative; model selection hardcodes MPS.

Fix: define warm-up/unknown semantics and test them. Advertise only tested platform/backend combinations until Windows capture and non-MPS model evaluation are validated.

## Validation record

Initial command: `python -m pytest tests -q` on Python 3.12.

Result before installing vision dependencies: **970 passed, 39 skipped, 1 failed, 8 errors**. Eight errors were missing `ultralytics` in sponsored-ad fixtures. The single pause-test failure passed in a targeted rerun with foreground polling mocked. Do not call this baseline fully green. Model-dependent and missing-corpus coverage must be reported separately from unit tests.

After installing vision dependencies, the final unmodified suite result was **976 passed, 39 skipped, 3 failed in 17.06s**. The same pause test failed; the other two failures require `datasets/state_v3/*/GruntBattleButton/*`, which is absent in this checkout, and therefore counted zero positive examples. They are missing-corpus failures, not evidence that the classifier rejected supplied positive frames. Installed evaluation versions included Ultralytics 8.4.178, Torch 2.14.1 and OpenCV 5.0.0.93; these are the audit environment, not a recommended production lock. The installation changed no repository dependency files.

Two small synthetic checks confirmed F1 and F2's decision behavior; a ScreenStabilizer check confirmed F11's first-frame behavior. No runtime source was changed during this audit.

## Delivery stages and acceptance criteria

1. **Reliability baseline:** fix F1/F2, isolate device services, add hermetic unit CI and a separate vision job. Regressions must fail before the fixes. No unexpected device calls in replay.
2. **Action/outcome contract:** action receipts, queue invalidation, conditional transitions and versioned event records. A failed command cannot count as an executed throw; unknown catch results remain unknown.
3. **Perception and evaluation:** curate encounter outcomes and stop rewards, hard negatives and different aspect ratios. Report held-out precision/recall and end-to-end action errors with sample counts. No promotion from recall alone.
4. **Calibrated gestures:** verified backend, measured latency, device profile and bounded trajectories. Keep straight-swipe fallback. Require ordered down/move/up, monotonic timestamps, bounded coordinates and reliable release/cancellation.
5. **Closed-loop autoplay:** one encounter and one stop cycle at a time, then bounded recovery and restocking. Explicitly report unsupported provider features. Defer inventory deletion, transfers and purchases.
6. **Supervised device comparison:** use the same device/settings and comparable sessions. Record confirmed catches per active hour, successful launches/requested throws, balls per confirmed catch, false target taps, confirmed stop rewards, recovery time and user interventions. Treat this as a proposed evaluation, not a result already achieved.

## Agent roster and models

Recommendations are workload judgments, not project-specific model benchmark results. Official model catalog checked on 17 October 2026: https://developers.openai.com/api/docs/models

| Role | Model / reasoning | Ownership | First deliverable |
|---|---|---|---|
| 1. Technical lead and integrator | GPT-6.1 Sol / high | Contracts, roadmap, shared runner/config changes and merges | Small staged backlog with ownership and acceptance gates |
| 2. Runtime reliability engineer | GPT-6.1 Sol / high | FSM recovery, runner/device integration, action lifecycle | F1/F2 regressions and fixes, hermetic device abstraction |
| 3. Perception and evaluation engineer | GPT-6.1 Sol / high | Observation/perception, data review, detector evaluation | Dataset manifest and honest class/outcome metrics |
| 4. Gesture and calibration engineer | GPT-6.1 Sol / high | Gesture planner, actuator backend, calibration | Tested trajectory protocol and straight-throw calibration |
| 5. Autoplay behavior engineer | GPT-6.1 Sol / high | Encounter/stop handlers and provider capabilities | One complete measured encounter and stop loop |
| 6. Independent test/review engineer | GPT-6 Astra / high for milestone review | Adversarial review, replay/fault harness and integration tests | Evidence-based review of failure paths and measurement claims |

Use Sol for the reviewer too if budget is tight; reserve Astra for architectural disputes and milestone reviews. GPT-6 Luna can assist with routine documentation or annotation-format checks, but should not be the sole approver of visual labels or control changes. Model availability in the user's picker is not established by the public API catalog.

Run lead + reliability first. After action/observation contracts are agreed, perception and gesture work can proceed on separate branches. Autoplay integrates their outputs afterward. Reviewer tests each integration. Do not let several agents rewrite `runner.py`, `fsm.py`, or shared dataclasses simultaneously. Prefer separate worktrees and small PRs; lead owns shared-interface edits.

## Shared starting prompt

Paste this before each role prompt:

```text
Work in zidanerick/PoGoBot. Read docs/AUTOPLAY_PROJECT_PLAN.md and any applicable AGENTS.md. The audited baseline is 78149d3ae124e84c604e99ac96ef91a193111de2; inspect current HEAD and preserve subsequent changes. Implement the assigned narrow milestone on an isolated branch/worktree. Preserve the pure Observation -> FSM -> Effects boundary, normalized coordinates, dry-run behavior and bounded recovery. Use injected clocks/device services for tests. Do not query a real device during replay or unit tests. Keep attempted actions, accepted commands, executed commands and confirmed game outcomes distinct. Never manufacture labels or catch results. Do not claim natural motion prevents bans. Do not add an LLM to the real-time loop. Do not perform transfers, deletions, purchases, account changes or external notifications as part of testing. Deliver a reviewable patch with focused tests, exact commands/results, changed interfaces, and remaining limitations. Coordinate shared-file changes with the lead. Do not merge your own work or launch other agents unless asked.
```

## 1. Technical lead: initial prompt

```text
Act as technical lead. Turn the audit into the smallest ordered implementation backlog. First establish action IDs, command lifecycle, observation timestamps, outcome evidence and provider capability contracts. Preserve compatibility where practical and document schema migrations. Assign one owner to shared runner/config/dataclass changes. Prioritize F1/F2 and hermetic tests, followed by action accounting and cancellation. Define acceptance cases before assigning perception, gesture and autoplay integration. Create docs/IMPLEMENTATION_BACKLOG.md with dependencies, explicit non-goals and merge gates. Do not start a broad rewrite or model training. Return the first three bounded implementation tickets and the exact interfaces other agents may build against.
```

## 2. Runtime reliability: initial prompt

```text
Reproduce and fix F1 and F2 first. Add regression tests through fsm.step and runner integration: exhausted foreground attempts must refuse screen gestures while still reaching restart/halt escalation; a continuously visible empty map must rotate after its configured idle interval without corrupting the recovery timestamp. Then isolate foreground queries behind an injectable device service, preserving Unknown on failed queries and eliminating ADB from replay/tests. Fix the pause-test isolation failure without relaxing the assertion. In a separate patch, implement the agreed action lifecycle and dependent-effect acceptance contract; prove rejected taps do not advance as successful interactions. Include blocked-worker tests for stale queued actions and pause cancellation. Keep commits small and report each reproduced failure before its fix.
```

## 3. Perception and evaluation: initial prompt

```text
Audit actual available images, labels and model metadata; do not assume datasets referenced by README exist. Define an annotation schema for ball-ready, launch, flight/result waiting, breakout, caught, fled, out-of-balls, stop disc/reward and Unknown. Separate observable evidence from inference. Build an evaluation tool with explicit class mappings, one-to-one matching, per-class precision/recall, negative-frame false positives and latency; remove hardcoded MPS selection. Enforce per-sample reviewed hashes in promotion and recording-level split separation. Add synthetic matcher/review-gate tests now. When real examples are missing, deliver the collection/labeling specification and an explicit data-needed report rather than fabricating accuracy. Keep gameplay policy out of perception.py and do not replace weights without held-out evidence.
```

## 4. Gesture and calibration: initial prompt

```text
Design touch gestures that are smooth, bounded and measurable. Inspect existing Swipe/Pinch effects and the Java injector before choosing a backend. Verify that any proposed backend supports continuous single-pointer down/move/up with predictable timing; do not approximate a curve with independent ADB swipes. Implement a pure timestamped trajectory planner with minimum-jerk/eased motion, bounded seeded variation, coordinate limits and replay logs. Variation must remain within the target's safe hit area and must never justify acting on uncertain perception. Add duration, velocity, timestamp, release and cancellation tests plus a preview/calibration tool. Start with straight throws and camera gestures, then design curve throws after transport validation. Separate host delay from actual gesture duration. Preserve existing fallback and clearly identify phone validation still required. Do not describe this as anti-detection.
```

## 5. Autoplay behavior: initial prompt

```text
After the lead approves observation/action contracts, implement one closed-loop encounter controller: wait for verified readiness, select a calibrated aim, dispatch one gesture, wait for execution and visible launch, then classify breakout/catch/flee/unknown before retrying. Use bounded retries and explicit timeouts; ambiguous animations must not trigger repeated swipes. Handle no-balls separately from inaccurate throws. Next add a stop controller that verifies the disc, spins only in the supported mode, and records a reward only with evidence. Introduce provider capabilities for external auto-spin/auto-battle/AutoWalk; unsupported features should be disabled with a clear reason. Preserve the existing assisted mode. Leave inventory deletion, transfers, purchases, independent navigation and full combat outside the first milestone. Demonstrate behavior on annotated replay sequences before live-device trials.
```

## 6. Independent reviewer: initial prompt

```text
Review the proposed milestone independently against docs/AUTOPLAY_PROJECT_PLAN.md. Focus on failed/rejected/late commands, stale frames, pause/resume, foreign foreground, unknown device state, queue backpressure, restart exhaustion, misleading success counters and dataset leakage. Exercise public dispatcher/runner paths rather than only helper methods. Build deterministic replay/fault tests with injected clocks and device services. Confirm zero ADB/network calls in offline tests, no pending screen action after cancellation, and no successful-catch claim without outcome evidence. Separate missing fixtures/dependencies from product failures. Review gesture claims against what the backend actually emits. Report only substantiated findings with reproduction, severity and a minimal fix; explicitly state untested live-device behavior. Do not silently edit another agent's patch or approve based on test count alone.
```

## First practical milestone

Start agents 1 and 2 with the prompts above. Their first reviewable result should be a green hermetic unit baseline plus the two confirmed recovery/rotation fixes. This is more valuable than adding random movement to the current fixed-swipe loop. The rest of the roles are staged assignments, not a recommendation to pay for six continuously running agents.

## Runtime reliability progress

On 18 October 2026, branch `fix/runtime-reliability` completed F1 and F2 with public-dispatcher and runner-loop regressions. F1 still refuses gestures while another app is foregrounded but now lets RECOVERING reach its restart/halt timeout. F2 tracks scan progress separately from `last_map_ts`, so repeated visible-map frames no longer reset the idle-rotation timer. Foreground `dumpsys window` checks now use an injectable nonblocking service; replay and an unconfigured runner use a no-device service, and failed/ambiguous reads remain UNKNOWN. UI-tree account reads remain synchronous and need their own timing design because SWITCHING and PREFLIGHT depend on fresh tree results.

Validation on this checkout: `python -m pytest tests --ignore=tests/test_sponsored_ad.py -q` passed **982 tests, 39 skipped**. The full suite could not collect the sponsored-ad model tests because `ultralytics`/Torch are not installed in this environment. This does not change the audit's historical test record above.
