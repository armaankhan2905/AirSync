# Stability repair — 2026-10-21

This repairs the existing application and preserves its eight-page GUI, encrypted profile, models, gesture mappings and Windows-lock implementation. No Air Writing or additional drawing gestures were added.

## Root causes and evidence

1. **Face-state instability.** Presence policy discarded its consecutive match count after every missed observation and had no authentication grace. Quality failures were included in the unknown-person count. Completed liveness expired after 60 seconds despite uninterrupted owner matches. Matching used one cutoff for both initial acquisition and retention. Runtime logs confirm repeated VERIFIED → UNKNOWN_PERSON/TEMPORARILY_MISSING transitions; previous logs lacked scores and timing, so they cannot prove which camera/pose caused each failure.
2. **Preprocessing/storage check.** Enrollment and verification both use the same YuNet landmarks, SFace alignCrop/feature extraction and unit normalization. Read-only aggregate inspection found 12 saved 128-dimensional samples, norms approximately 1.0, and a within-profile median cosine similarity of 0.657. No normalization/storage corruption was found. This does not establish recognition accuracy against unseen owner poses or other people.
3. **Office visibility.** Discovery returned None whenever the foreground class was not in a short Office class allowlist. It did not independently check running Office processes. COM failure also collapsed discovery to None. Earlier live inspection found an open presentation but zero slideshow windows; an editor alone cannot receive slideshow navigation.
4. **Gesture execution.** Recognition loss disarmed the temporal filter, requiring another neutral hand. Missing Office/slideshow readiness prevented submission. Command metadata was incomplete; a full queue was silently dropped, and rejection/result messages were overwritten by status polling. There was no complete trace from classifier to COM outcome.
5. **Synchronization/performance.** GUI, watchdog and inference used separate mutable fields. Queued updates could describe older states. Face and hand inference were serial, so slow face processing delayed hands. Per-frame inference exceptions escaped the worker. The old captured/status design did not provide a single versioned authentication snapshot.
6. **Locking configuration.** The inspected saved profile had auto-lock disabled and simulation enabled. That configuration intentionally cannot lock Windows; the repair does not silently enable it.

## Implemented changes

- Acquisition keeps the 0.53 default cutoff and three fresh consecutive observations. Verified retention uses a separate default 0.03 hysteresis band (0.50 retention cutoff).
- A configurable 1.2-second verification grace tolerates benign absence/quality interruptions. It does not extend itself or reset the absence timer. Clear nonmatches, camera/model failures, session/context changes and sustained absence revoke controls. Grace must be shorter than the absence timeout; zero grace is supported.
- Fresh owner matches renew completed liveness, avoiding a forced challenge every minute. Sustained loss/camera/session/pause events still require a new challenge.
- `StateManager` supplies immutable copied snapshots, monotonic capture watermarks, revisions and command-authentication epochs. Late results and replaced-worker GUI updates are rejected. Preview visibility changes do not reset the security timer.
- Face inference runs in its own worker with a latest-only mailbox/result slot. Camera, hand orchestration and Office remain independent. Qt consumes a single latest image and current state on its main thread. Per-frame face/hand errors recover without silently authorizing a failed frame.
- Office inventory uses process executable names, HWND/PID/class metadata, registry installation evidence and COM collections. PowerPoint running/no slideshow remains visible in the background. A single slideshow on another monitor can be targeted while PowerPoint is foreground; ambiguous multiple shows require a matching slideshow window.
- COM initializes/executes/uninitializes on one STA thread. Discovery refreshes before dispatch. No Office object crosses threads and no keyboard fallback was added.
- Immutable gesture events include all requested metadata. Dispatch validates readiness, stable/authorized flags, confidence, handedness, authentication epoch, target/focus, command validity, duplicate/order and expiry. Queue overflow and rejected/executed commands have explicit results.
- Existing left/right L, fist/pinch mappings and neutral/cooldown behavior remain. PowerPoint Exit only exits the slideshow; Word pause can be resumed by mapped pinch.
- Dashboard → Developer Diagnostics exposes face scores/thresholds/grace, transition reasons/timestamps, Office process/COM/slideshow states, gesture authorization/stabilization/commands/results, lock policy and measured inference durations. Logs omit embeddings, frames, passphrases, document contents and window titles.

## Modified and added files

Production:

- `visionguard/authentication.py`: hysteresis and continuously renewed liveness.
- `visionguard/config.py`: validated grace/hysteresis/diagnostic defaults.
- `visionguard/security.py`: temporal policy, grace, reasons and recognition-error state.
- `visionguard/state.py` (new): synchronized versioned application state.
- `visionguard/face_worker.py` (new): independent bounded face inference.
- `visionguard/windows_office.py` (new): read-only Windows process/window/installation inventory.
- `visionguard/office.py`: process-aware detection, event dispatch, STA handling and results.
- `visionguard/gestures.py`: stabilization diagnostics and event source metadata.
- `visionguard/runtime.py`: connects the repaired workers, state and dispatch pipeline.
- `visionguard/ui.py`: optional diagnostics, safe snapshot consumption and security controls.

Tests:

- Updated `tests/test_authentication.py`, `tests/test_camera_runtime.py`, `tests/test_runtime_pipeline.py`, `tests/test_ui_models.py`.
- Added `tests/test_state_manager.py`, `tests/test_face_worker.py`, `tests/test_office_discovery.py`, `tests/test_office_threading.py`, `tests/test_office_windows_adapter.py`.
- Updated `docs/test-results.xml` and supporting README/architecture/troubleshooting/manual-verification records.

## Automated and safe Windows checks

The repair suite passes **137 tests**. Lock APIs are mocked or simulated. Office command tests use mocked COM documents; face/hand positive tests use synthetic data. Real models are loaded for blank-input plumbing tests, not accuracy claims. The suite tests grace/hysteresis, continuous liveness, true absence and unknown replacement, stale state/result rejection, concurrency, camera failures, face-worker recovery/backpressure, process-aware Office discovery, multi-monitor slideshow selection, left/right events, gesture holds/rearming/cooldown, queue rejection, command expiry/epoch checks and same-thread COM lifecycle.

Compilation and `pip check` pass. A real Windows read-only Office inventory check correctly reported both applications installed and neither currently running. Existing enrollment was inspected only through aggregate statistics. Actual camera/gesture accuracy, real Office navigation, multi-monitor behavior, long-run performance and real Windows locking remain manual tests.

The rebuilt portable executable also passes offline diagnostics and the offscreen GUI smoke test (both exit 0). It was reopened visibly on the Windows desktop in forced simulation with the existing profile. Initial live observations reported no detected face, including a startup camera-error interval followed by recovery to fresh frames. Subsequent no-face detection times were approximately 36–44 ms with result ages around 127–148 ms. These are limited no-face runtime observations, not owner-recognition/hand performance measurements or proof that the physical verification checklist passes. No real Office commands or Windows lock were sent during this repair.

## Launch

From the project root:

```powershell
# Repaired portable app; keep the entire distribution folder together.
.\dist\VisionGuardAI\VisionGuardAI.exe --simulation

# Run source using the existing environment.
.\.venv\Scripts\python.exe main.py --simulation

# Automated regression suite (all real locking is mocked/simulated).
.\.venv\Scripts\python.exe -m pytest -q
```

For a later explicitly authorized actual-lock test, launch without `--simulation`, then enable auto-lock and disable Development simulation through protected Security Settings. Saving requires the app recovery passphrase and a separate real-lock confirmation.

## Laptop verification sequence — pending

Record PASS/FAIL, timing and diagnostic reasons; do not infer success from a green indicator alone.

1. Launch in simulation and enable Dashboard → Developer Diagnostics.
2. Use the existing enrollment, or redo it in good light if it no longer represents your normal pose. Complete the randomized verification challenge.
3. Remain visible for at least three minutes while blinking, speaking and turning moderately. Confirm continuous verification does not force a new challenge each minute. Check similarity and face-inference age. Brief benign interruptions should show bounded grace; persistent failure must not remain verified.
4. Open the Windows desktop PowerPoint app and a sample presentation with at least three slides. Keep VisionGuard foreground once; confirm it still detects the PowerPoint process and shows **PowerPoint Running — No Active Slideshow**.
5. Check the diagnostics: PowerPoint running, COM connected, slideshow count zero. A COM unavailable result is a failed integration check, not a working target.
6. Press F5 in PowerPoint to start the slideshow.
7. Confirm slideshow count and ready target. Focus PowerPoint or the intended slideshow. On two monitors, test the actual presenter/slideshow arrangement; use one slideshow first.
8. With your face verified, open your palm for about one second, then hold a right-hand L. Confirm exactly one next-slide action and its execution result. A continuing hold must not advance repeatedly.
9. Open your palm again, then hold a left-hand L. Confirm one previous-slide action.
10. Rearm and show a fist. Confirm slideshow exit. Repeat using pinch in a new slideshow.
11. Confirm PowerPoint and its presentation remain open. Switch to an unrelated app and confirm gestures cannot change slides.
12. Open a sample Word document in Print Layout. Test right/left page scrolling, fist pause, pinch resume, and preserved selection. Test Read Mode separately as one-screen scrolling.
13. Test genuine absence beyond the timeout and an unknown-person replacement, starting with simulated auto-lock. A clear unknown face must immediately suspend gestures and never renew the owner timer. Test short dropout separately from unknown replacement.
14. Only after the preceding checks pass and the device owner explicitly approves an actual lock: save documents, have the Windows PIN/password ready, relaunch without the simulation flag, choose a conservative timeout, enable real auto-lock through protected settings and confirm. Leave the camera view. Confirm one Windows lock, then unlock normally through Windows. VisionGuard never unlocks Windows.

If a test fails, capture the sanitized Developer Diagnostics text and the corresponding local log timestamps. Do not send face embeddings or private documents.

## Limits

RGB head movement is basic liveness and can be spoofed. Recognition/gesture thresholds have not been calibrated against a population or this laptop's full range of conditions. Grace deliberately retains recent authentication for its bounded duration, including a brief no-face observation; use zero grace if immediate dropout revocation is required. Additional faces always block Office actions. Hand ownership cannot be proven. Multiple Office instances/custom shows may be refused. Native camera/model/COM hangs can outlive bounded joins, and a normal desktop app cannot survive being killed or replace Windows authentication.
