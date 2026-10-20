# Supervised Android input smoke test

This checklist validates the injector on an explicitly supervised Android device. Run
against a disposable touch-visualization/test surface that records `MotionEvent`s. Do not
open or operate Pokémon GO during this test. The gesture transport remains isolated from
gameplay until the lead accepts the shared action and identity contracts and the
integration gate.

## Record before testing

- [ ] Operator, date/time, supervision method, and device owner authorization recorded.
- [ ] Device model, Android release/API level, build fingerprint, display size/density,
      orientation, and ADB serial recorded.
- [ ] Injector DEX SHA-256 and build tool versions recorded. Confirm the DEX class check
      reports `Gesture`, `Gesture$Point`, and `Gesture$MotionAction`.
- [ ] Test surface is foregrounded and visibly logs pointer ID, action, coordinates,
      `downTime`, and `eventTime` for every received event.
- [ ] Display geometry/calibration is measured for this session. Use a path well inside
      the test surface bounds.
- [ ] Agree on timing tolerances before the run, based on the device and test surface.
      Do not tune tolerances after seeing the results.

## Startup

- [ ] Start the injector once and verify `READY` arrives within the configured startup
      timeout, before sending any plan.
- [ ] Verify the test surface receives no touch before a valid plan is submitted.
- [ ] Repeat with startup deliberately blocked or unavailable. Verify the host reports a
      startup failure, submits no plan, closes both pipes, and reaps or terminates the
      injector process within the cleanup bound.

## One-pointer continuity

- [ ] Submit one short, in-bounds plan with at least three samples.
- [ ] Verify exactly one `ACTION_DOWN`, ordered `ACTION_MOVE` events, and exactly one
      `ACTION_UP` are received for pointer ID 0.
- [ ] Verify pointer count remains one, pointer ID is unchanged, `downTime` is constant,
      and `eventTime` increases strictly across the gesture.
- [ ] Verify coordinates match the calibrated samples within the pre-agreed pixel
      tolerance. Verify no second DOWN, extra pointer, duplicate terminal event, or
      out-of-bounds point appears.
- [ ] Verify the injector returns `START` and `DONE` receipts and exits successfully.

## Cancellation

- [ ] Submit a longer plan and request cancellation after DOWN and at least one MOVE.
- [ ] Verify one `ACTION_CANCEL` terminates the same active pointer, with no later MOVE or
      UP. Verify the injector returns `CANCELLED` and exits.
- [ ] Repeat cancellation before submitting a plan. Verify no DOWN is delivered.
- [ ] Verify a second plan can start after cancellation, with no pointer or process state
      carried over from the first run.

## Cleanup and failure handling

- [ ] After successful completion and after cancellation, verify the host process exits,
      stdin/stdout close, and no touch remains held on the test surface.
- [ ] Force a controlled injector-side rejected event and verify an `ERROR` receipt,
      visible test failure, process exit, closed pipes, and no stuck pointer.
- [ ] Suppress a terminal receipt and verify the host timeout, cancellation/termination
      cleanup path, closed pipes, and no stuck pointer.
- [ ] If device disconnection is part of the test, record it as a separate fault case;
      reconnect and verify the host reports failure rather than success and the next
      supervised run starts cleanly.

## Timing and results

- [ ] Run at least five successful plans with the same geometry and timing parameters.
- [ ] For each run, record requested duration, device `START`/`DONE` timestamps, observed
      event timestamps, host submission-to-`START` delay, maximum inter-event lateness,
      and any missing or rejected event.
- [ ] Keep host startup/queue delay separate from device-side contact duration. Compare
      each measurement with the tolerances agreed before testing.
- [ ] Record pass/fail per check, logs and test-surface captures, any device/app state
      changes, and the operator. Any missing event, pointer discontinuity, stuck touch,
      unexpected app interaction, cleanup failure, or out-of-tolerance timing is a fail.

Passing this checklist demonstrates only the observed injector behavior on the recorded
device and test surface. It does not validate gameplay behavior, throw outcomes, or
accuracy, and does not authorize gameplay integration. Gameplay wiring remains disabled
until the lead accepts the shared action/cancellation and identity (`FrameRef`) contracts
and the integration gate.
