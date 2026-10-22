# Implementation and verification record

**2026-10-22 persistent multi-user update:** the current source passes **236 tests**. See [PERSISTENT_USERS.md](PERSISTENT_USERS.md) for storage migration, administrative registration, automatic recognition, backend Office permissions, separate workstation trust, GUI renders and manual Windows checks. Historical single-owner/dashboard results below describe earlier implementations.

**2026-10-22 blur-rejection follow-up:** the current source passes **176 tests** after separating live/enrollment focus requirements and fixing incomplete challenge resets during brief quality dropouts. See [FACE_QUALITY_REPAIR.md](FACE_QUALITY_REPAIR.md) for numeric camera evidence, security-preserving behavior and remaining end-to-end checks.

**2026-10-22 camera interface update:** the current source has a single camera canvas, compact settings and confirmed-action notifications. The expanded suite passes **166 tests**. See [CAMERA_UI_REDESIGN.md](CAMERA_UI_REDESIGN.md) for modified files, exact scope, inspected screenshots and remaining manual checks. The existing packaged executable predates this source redesign. Older dashboard screenshots and build checks below are historical records.

VisionGuard AI is implemented as a connected desktop application. **It is not fully verified for real hardware or real Office use.** All remaining physical checks are explicitly unperformed in [MANUAL_TESTS.md](MANUAL_TESTS.md).

## Automated result

**137 passed, 0 failed.** Executed with the isolated Windows CPython 3.13.3 environment using:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp\repair-tests --junitxml=docs\test-results.xml
```

Machine-readable results: [test-results.xml](test-results.xml). Tests cover synthetic identity matching and enrollment, quality rejection, randomized liveness/expiry/dropouts, presence timing and unknown replacements, strict privacy, camera failure, gesture geometry/handedness/debouncing/rearming, Office targeting/navigation/boundaries/exit, storage corruption, scrypt authorization/rate limits, startup command construction, session API wrappers, watchdog behavior and connected worker orchestration. GUI tests construct/navigate all eight pages offscreen without camera or Office actions.

Safe integrations exercised on this actual Windows development machine:

- Windows DPAPI protects/unprotects test-only local data.
- All official models pass pinned SHA-256 checks.
- YuNet, SFace and MediaPipe Hand Landmarker load and run real CPU inference on blank synthetic inputs. This tests model/runtime plumbing, **not positive face/hand accuracy**.
- PySide6 renders the dashboard offscreen with the installed Windows font. [Dashboard image](dashboard.png).
- `pip check` reports no broken dependencies.
- PyInstaller produces `dist\VisionGuardAI\VisionGuardAI.exe`, with models, native dependencies, model notices, package license texts, and documentation.
- The portable executable passes safe offline diagnostics; [packaged-diagnostics.json](packaged-diagnostics.json) records frozen-runtime model inference, DPAPI and COM imports. No camera acquisition, Office commands or actual Windows locking is part of this test.
- Source diagnostics are in [source-diagnostics.json](source-diagnostics.json). A packaged offscreen GUI image is saved as `packaged-dashboard.png` after the executable smoke check.

All automated lock calls use a mock or explicitly labeled simulation. Camera/Office positive-operation tests use mocks. No automated test calls real `LockWorkStation`, modifies startup registry entries or sends commands to a live document.

## Feature verification matrix

The current repair record is [STABILITY_REPAIR.md](STABILITY_REPAIR.md), including exact root causes, changed files, the expanded 137-test scope and the laptop verification sequence. New positive Office command tests use mocked COM objects. A real read-only Windows inventory check found Word and PowerPoint installed and neither process running at that check. Native navigation and physical gesture accuracy remain unverified by automation.

[feature-matrix.json](feature-matrix.json) contains a record for every major feature with:

- Implemented: Yes/No.
- Automated test status and exact scope.
- Real Windows test status, distinguishing safe APIs/offscreen execution from real camera/Office/lock behavior.
- Known limitations.
- Remaining manual test IDs.

Every requested feature has an implementation. The safe Windows API/model/build checks passed; **webcam enrollment and real recognition, physical gestures, actual workstation locking, Word/PowerPoint behavior, tray interactions, startup, sleep/resume and sustained performance still need manual testing**.

## Material limitations

### Follow-up after user testing (2026-10-21)

The user completed face verification and reported motion-related verification loss, no PowerPoint actions, and no absence locking. Local logs confirm successful VERIFIED states interspersed with UNKNOWN_PERSON/TEMPORARILY_MISSING. Read-only checks found auto-lock disabled, simulation enabled, one open PowerPoint presentation and zero slideshow windows. A normal visible desktop launch is confirmed. These observations do not establish positive gesture or locking accuracy.

The subsequent full repair replaces immediate benign-dropout revocation with configurable authentication grace and retention hysteresis, renews liveness on continuous matches, separates face inference from hands, centralizes versioned state and adds process-aware Office discovery plus traceable command events. Regression tests cover these changes and stale-event rejection. See STABILITY_REPAIR.md for the current behavior; physical motion tolerance and real gesture actions still need user retesting.

### Remaining limitations

1. Random head turns are basic RGB liveness, not certified anti-spoofing. A replay or sophisticated presentation may pass. No calibrated false-acceptance/false-rejection or demographic evaluation is claimed.
2. A normal Windows user process can be stopped by that account or malware running as it. An in-process watchdog cannot survive total process death/hang. This supplements Windows authentication and never unlocks Windows.
3. Office commands depend on conservative foreground HWND matching and responsive COM. Custom PowerPoint show navigation is refused; Presenter View/multiple instances may be refused. Word Read Mode uses one screen rather than exact physical page navigation.
4. Hand ownership cannot be established from hand landmarks. Extra visible faces suspend gesture actions; an off-camera person's hand remains a residual limitation.
5. Camera/CPU performance, gesture geometry and face quality need tuning on the user's device. Native driver or COM calls can block beyond normal bounded shutdown waits.
6. Recognition/liveness is required after meaningful absence or session/stall invalidation. With real auto-lock armed, a short timeout can lock while a challenge is incomplete. Select and test the timeout before real locking.
7. The executable is an unsigned portable one-folder build, not an installer. Other-PC Windows verification and redistribution-license review remain separate release steps.

See [ENVIRONMENT.md](ENVIRONMENT.md) for the observed environment and [ARCHITECTURE.md](ARCHITECTURE.md) for design/security details.
