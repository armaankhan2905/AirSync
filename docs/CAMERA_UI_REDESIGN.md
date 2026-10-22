# Camera-first interface — 2026-10-22

The source application now opens into a single camera canvas. The sidebar, stacked pages, dashboard metrics and permanent instruction panels have been removed.

## Modified files

- `visionguard/ui.py`: compact status and activity overlays, in-camera enrollment, gear settings, recent-event tooltip, blocked-gesture feedback, Office result notifications and updated tray actions. Calibration, Office mappings and diagnostics are available under collapsed More options. Security-sensitive saves, re-enrollment, pause, restart and exit retain recovery authorization.
- `visionguard/camera_view.py` (new): one cached native-size video pixmap, aspect-preserving paint-time scaling, resize support and an animated non-focusable notification lasting about 1.8 seconds. Full camera field of view remains visible; differing window/video aspect ratios produce fit bands instead of distortion or cropping.
- `visionguard/runtime.py`: UI snapshot fields and a command-result signal; per-face verification colors, connected hand landmarks and readable labels without confidence scores. Face coordinates are mirrored once; hand coordinates already belong to the mirrored frame. Stale face boxes are suppressed. Capture, inference, gesture classification, security policy and authorization workers are reused.
- `visionguard/office.py`: original gesture identity accompanies completion/rejection feedback. Successful notifications are limited to controller confirmations. Slide boundaries and a revoked final guard now return informative non-success results without changing navigation or security guards.
- `assets/settings.svg` (new): a vector settings icon that does not depend on font glyph availability.
- `tests/test_camera_interface.py` (new), `tests/test_ui_models.py`, `tests/test_office.py`: new UI regressions and updated assertions for the removed pages.
- `README.md`, `docs/MANUAL_TESTS.md`, `docs/VERIFICATION.md`, this document and the two UI screenshots: current navigation, verification scope and manual checks.

## Verification

Full automated suite: **166 passed**. Includes real model initialization/blank-frame inference and existing mocked authentication, presence, gesture, lock, Office, startup and storage checks. No automated check issues real workstation locks, writes startup registry entries or commands a live Office document.

New regressions cover:

- One camera canvas with no list sidebar or stacked pages, including normal, minimum, maximized-size and portrait layouts.
- Correct video aspect ratio and full frame visibility on resize.
- Verified/unknown/verifying status, per-person face-box labels and mirrored geometry, connected hand landmarks and anatomical L labels.
- Camera failure clearing stale video and rejection of replaced-worker/stale-revision updates.
- Enrollment prompts and progress on the canvas, enrollment completion, and authorization for re-enrollment.
- Settings cancellation without mutation, protected saves and forced simulation preservation.
- Gesture detection without false execution claims, one blocked warning per hold, repeat confirmations for distinct executions, timed notification disappearance, failed commands, slide boundaries and final-guard cancellation.
- Monitoring/frame consumption and Office feedback while hidden, close-to-tray without stopping workers, a bounded five-event history and optional diagnostics confined to settings.

The application smoke command also exited successfully:

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir .tmp\camera-ui-smoke --screenshot docs\camera-interface.png
```

Offscreen renders of the main interface, compact settings, action toast and minimum-size layout were visually inspected. [Initial interface](camera-interface.png) and [compact settings](camera-settings.png) are safe renders without camera access. Action-layout inspection used a clearly synthetic frame and injected Office result; it is not evidence of a real Office action.

Real webcam enrollment/accuracy, anatomical hand accuracy, physical gesture execution in PowerPoint/Word, actual Windows locking, native tray behavior and sustained CPU performance still require the existing hardware checklist and U01–U06 in MANUAL_TESTS.md.

## Running the updated source

Exit any old running instance through its protected tray Exit action, then run the documented `main.py --simulation` command. Existing enrollment and protected settings stay in the normal profile. The existing `dist/VisionGuardAI` executable has not been rebuilt; it retains its previous interface until rebuilt using the documented Windows build script.
