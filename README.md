# VisionGuard AI

**Intelligent facial authentication and touchless workspace control for Windows.**

A Python / PySide6 desktop application that automatically recognizes locally registered users, separately monitors the device owner/trusted presence, requests Windows locking after an absence, and controls the foreground PowerPoint slideshow or Word document with hand gestures. All inference runs locally on the CPU.

**Verification status:** implemented and automated checks are recorded in [docs/VERIFICATION.md](docs/VERIFICATION.md). Real webcam enrollment, recognition accuracy, gestures, Windows locking, and Office operation require the manual checks in [docs/MANUAL_TESTS.md](docs/MANUAL_TESTS.md). Do not treat this application as fully hardware verified.

## Quick start on this machine

Open PowerShell in this project directory:

```powershell
.\.venv\Scripts\python.exe main.py --simulation
```

The supplied `.venv` and downloaded models are local setup artifacts; create your own environment on another computer. Development simulation logs lock requests without locking Windows. It does **not** simulate the camera, face recognition, gestures or Office integration. Auto-lock is disabled initially.

## Install from source

Windows 11 x64 is the target. Windows 10 is intended but unverified. Tested development interpreter: CPython 3.13.3 x64. Python 3.11/3.12 should be compatible with the pinned wheels but have not been verified here. No GPU is required. Word and PowerPoint desktop installations are needed for their integrations; Office web apps are unsupported.

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\fetch_models.py
.\.venv\Scripts\python.exe main.py --simulation
```

If `python` is an unrelated Anaconda installation, use the full path to your selected CPython executable when creating `.venv`. Do not install both `opencv-python` and `opencv-contrib-python`; MediaPipe uses the contrib distribution and this project pins it explicitly. `requirements-lock.txt` records the tested full environment.

Setup requires internet only for installing packages and downloading three official models. Downloads are pinned to an OpenCV Zoo commit / a MediaPipe model version and verified against SHA-256 hashes in `models/manifest.json`. Runtime never downloads anything. Verify offline files with:

```powershell
.\.venv\Scripts\python.exe scripts\fetch_models.py --verify-only
```

## Persistent registration and automatic recognition

Use **+ Register User** beside the gear icon. Registration requires the local administrator recovery passphrase and captures multiple quality samples through the existing camera worker. Names and permissions live in SQLite; biometric templates are protected with Windows DPAPI. Registrations survive restarts and reboots. Matching starts automatically, with fresh liveness and consecutive observations; there is no manual Verify button or routine re-enrollment.

**Settings → Manage Users** supports roles, per-app access, disabling, deletion, re-enrollment and explicit workstation-presence trust. A presentation user does not automatically gain permission to keep Windows unlocked. Gestures require exactly one verified and permitted person. Existing enrollment migrates into an administrator named **Device Owner**, preserving the recovery credential and face templates.

See [persistent-user guide and manual checks](docs/PERSISTENT_USERS.md). The current suite passes **306 tests**. Physical multi-user/Office behavior remains to be tested, and RGB head-turn liveness cannot guarantee rejection of all photo/video spoofs.

## Air Writing on PowerPoint

Open **Settings → Air Writing** to enable the feature, set pen color/thickness, adjust smoothing and confidence, and calibrate four comfortable reach corners. The main screen remains focused on the camera.

With a verified, permitted user and a focused slideshow, show **two fingers (index + middle)** to rearm, then hold **three fingers (index + middle + ring, thumb/little folded)** for 0.5 seconds to toggle writing. Rearm, then use **only the index finger** to draw. Rearm and hold **all five fingers** for 0.5 seconds to clear only the current slide's ink. Hold gestures fire once until neutral rearming. L navigation is suppressed while writing is on; fist/pinch keep their existing exit mappings.

Ink is a transparent, click-through overlay, stored per slide in memory and discarded when its show closes. PowerPoint content stays unchanged. Face or camera loss stops input immediately. See the [Air Writing guide, implementation details and hardware checklist](docs/AIR_WRITING.md). **Real webcam/PowerPoint verification is still required**; automated checks and screenshots are synthetic.

## Camera-first interface

The application opens into one live camera canvas with no sidebar or dashboard cards. The compact top overlay shows verification, Office target and gesture state. Face boxes and hand skeletons appear directly on the mirrored video. A fading action confirmation appears only after Office confirms a command; blocked gestures explain why they could not act. The bottom overlay keeps the latest event, with five recent events in its tooltip.

Use the gear button for compact protected settings. **More options** keeps calibration, mappings and diagnostics available without filling the camera screen. If an older instance is running, exit it through its protected tray menu before launching the updated source. The existing `dist` executable needs rebuilding to include source changes.

## First launch and face enrollment

1. Consent to local camera access. Windows may additionally require **Settings → Privacy & security → Camera → Let desktop apps access your camera**.
2. Create and safely retain an **local administrator recovery passphrase of at least 12 characters**. This is separate from your Windows password. Protected settings, biometric deletion/replacement, pause, restart and exit require this passphrase.
3. Select **Register Administrator** on a new profile, or **+ Register User** for another account. For an existing account use **Settings → Manage Users → Re-enroll face**. Stand alone in even lighting; keep your entire face in the preview.
4. Follow the randomized turn-left, turn-right and face-center prompts. Directions refer to the mirrored preview. Six or more quality samples covering three poses are needed. Images are not saved.
5. Wait for enrollment confirmation. Complete the fresh head-turn challenge to verify yourself in the application.
6. Watch for **VERIFIED** in the top camera bar. Choose a conservative recognition threshold in **Settings → More options** and test impostor rejection and your lighting conditions before enabling real locking.

The face matcher compares SFace cosine embeddings against the closest two samples per registered user; competing identities must be sufficiently separated. The default acceptance cutoff is **0.53** (threshold 0.50 + rejection margin 0.03). This is a conservative starting value, **not a measured false-acceptance guarantee**. Three new face observations are required for VERIFIED. Face inference runs at up to five times per second, subject to CPU capacity.

Randomized head movement provides basic liveness. Fresh owner matches now renew the completed lease, so continuous presence does not require a challenge every minute. Sustained identity loss, camera failure, pause and lock/unlock still reset it. Initial matching retains the default 0.53 cutoff and three consecutive observations; verified retention uses the configurable default 0.03 hysteresis (0.50 cutoff). A configurable default 1.2-second verification grace tolerates brief missing/poor-quality observations. Grace cannot extend itself or reset the absence timer; clear nonmatches and camera/model failures revoke controls immediately. Keep the initial challenge visible and choose an absence timeout long enough to complete it. RGB liveness remains vulnerable to sophisticated spoofs.

## Test presence-based locking

1. Keep your Windows PIN/password available and save your documents.
2. In the gear settings, enable auto-lock while keeping **Simulate Windows locking** checked. Authorize and save.
3. Verify yourself, then step away. The default five-second authorized-owner absence timer must request a **SIMULATED workstation lock**. An unknown replacement must not stop it.
4. Pause/resume or restart monitoring (both protected) to reset a simulated lock. A simulated lock remains latched so one absence produces one request.
5. Only after the manual simulation checks pass, launch normally without `--simulation`, clear **Simulate Windows locking**, authorize, and explicitly confirm real locking. The forced `--simulation` flag cannot be disabled during that run.
6. Repeat the documented actual-lock manual test. `LockWorkStation()` locks Windows. Unlock normally through Windows; VisionGuard never unlocks or handles Windows credentials.

**Camera policy:** `lock` uses the same absence timeout when capture is lost, blocked or frozen; `alert` suspends controls and shows the error without camera-failure locking. Unknown faces still cause absence locking. Brief benign face interruptions can retain verification only within the configured grace; set grace to zero for immediate dropout rejection. Returning identity must satisfy consecutive verification before resetting the absence timer.

**Additional faces:** relaxed mode allows a verified owner's existing liveness lease to preserve presence; strict privacy counts an additional unknown face as a lock-triggering condition. **Office gestures always suspend whenever more than one face is visible**, because hand landmarks alone cannot prove which person owns a hand. A liveness challenge itself requires one face.

## Gesture calibration

Verify your face and watch the labels beside your hands. Open **Settings → More options** for calibration:

- Make an **L with your anatomical left hand**: index and thumb extended, approximately perpendicular; other fingers folded. The label should show `LEFT L`.
- Repeat with your anatomical right hand; the label should show `RIGHT L`.
- If both labels are reversed for your camera, enable **Swap left/right labels**, authorize, and save. The preview and hand inference both use mirrored input; screen position is never used to infer handedness.
- Open/relax your hand to a confidently recognized neutral pose for three observations before each action. A held gesture fires once. Losing sight of a hand does not rearm it.
- Fist and pinch require a hold of at least 450 ms, consecutive detections, and cooldown. L gestures require at least 180 ms and consecutive detections. Default cooldown is 700 ms.
- Tune minimum confidence, consecutive frames, cooldown and normalized pinch distance. Geometry confidence is a rule-based score combined with handedness confidence, not a calibrated probability.

Calibration while VisionGuard has focus is detection-only. No Office command is issued until a supported Office window has foreground focus.

## PowerPoint and Word

**PowerPoint:** detection now independently identifies installation, POWERPNT.EXE, editor/slideshow windows and COM status. The top bar shows **POWERPOINT** when it is detected; attempting a gesture without a ready show displays **NO ACTIVE SLIDESHOW**. Press F5, focus PowerPoint, verify your face and show a neutral hand. Left L moves back; right L moves forward; fist or pinch exits only the slideshow. A single slideshow on another monitor can be selected while a PowerPoint presenter/editor window is foreground. Multiple shows require matching slideshow focus; custom shows may be refused. Native multi-monitor behavior still needs manual verification.

Enable **Settings → More options → Show diagnostic details here** to inspect recognition scores, thresholds, grace/reasons, Office process/COM state, gesture stabilization/authorization, command results and inference timings. [Stability repair and laptop test sequence](docs/STABILITY_REPAIR.md) records the changes and remaining checks. Diagnostics contain no embeddings, camera images, passphrases or document/window titles.

**Word:** focus the document. Left/right L scrolls one page using `ActivePane.PageScroll` in Print Layout or Web Layout; cursor and selection stay in place. Read Mode uses `LargeScroll` for one screen because Word does not support PageScroll there. Other views show guidance. Fist pauses Word navigation; pinch toggles that pause. The global gesture switch must remain enabled for pinch to resume Word. You can edit mappings in **Settings → More options** and save with authorization.

Switching application or losing face verification cancels an in-progress gesture and requires neutral rearming. Queued commands expire after 800 ms and recheck the foreground target and authorization. The implementation uses COM only; no blind keyboard fallback sends input to unrelated applications. Busy, missing, or disconnected Office COM servers produce status messages and retry.

## Background operation and privacy

- Closing the main window minimizes to the visible tray by default. The tray opens the camera, toggles gestures, opens settings, and offers protected pause/exit.
- A background launch keeps the liveness challenge visible initially, then minimizes after successful verification.
- Hide the preview without stopping inference. Pause and session locking release camera access after the current capture operation, and resume reopens it. A blocked camera driver may delay cleanup; see troubleshooting.
- Optional per-user startup uses the Windows `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry `VisionGuardAI`. It runs only after user sign-in, never before Windows authentication. Disable it from the gear settings or Windows Startup Apps.
- Data lives in `%LOCALAPPDATA%\VisionGuardAI`. SQLite contains user metadata and audit events, DPAPI-protected settings, a salted scrypt administrator-passphrase hash, protected permission policies and user-bound encrypted face templates. DPAPI is scoped to the current Windows account; there is no plaintext fallback.
- No camera frames, raw face images or Office document contents are saved. Rotating diagnostic logs retain up to four ~1 MB files. User deletion uses SQLite secure deletion and VACUUM, but SSD/cloud/OS backups may retain previous database versions.
- This is a regular, visible desktop application, not a tamper-resistant Windows security service. Same-account malware or a user with Task Manager can terminate it. It supplements normal Windows authentication. Manual Windows lock remains available at all times.

## Automated tests and GUI smoke check

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir .tmp\smoke-profile --screenshot docs\camera-interface.png
.\.venv\Scripts\python.exe main.py --self-test docs\source-diagnostics.json
```

Tests mock all workstation-lock calls and use mocked camera and Office objects. Real model initialization and blank-frame inference run without opening a camera. The Windows DPAPI roundtrip uses Windows encryption, with test-only data. GUI tests run offscreen with no camera, COM commands, startup registration or locking. See [docs/MANUAL_TESTS.md](docs/MANUAL_TESTS.md) for real-world checks; these are deliberately not pre-marked as passed.

## Build a Windows executable

```powershell
.\scripts\build_windows.ps1
# Or:
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm VisionGuard.spec
```

The one-folder build is `dist\VisionGuardAI\VisionGuardAI.exe`. Copy the **entire folder** to another Windows x64 machine. It includes models, model license texts, Qt/MediaPipe runtimes and documentation. Do not copy an enrolled profile to another Windows account. The build is unsigned; review/sign it before external distribution. Follow Qt LGPL and other third-party license requirements. This is a portable distribution, not an installer or a Windows service.

## Project layout

```text
AirSync/
  main.py                       desktop entry point; explicit smoke/simulation modes
  pyproject.toml, requirements.txt, requirements-lock.txt
  VisionGuard.spec               Windows one-folder build
  visionguard/
    config.py                   validated settings and paths
    storage.py                  SQLite, DPAPI, scrypt authorization
    authentication.py           YuNet, SFace, quality, liveness, enrollment
    users.py                    encrypted user registry, permissions, administration and migration
    recognition.py              automatic multi-user matching and per-face liveness tracks
    user_dialogs.py             compact registration and user-management dialogs
    security.py                 presence state machine, lock adapter, session detection
    camera.py                   latest-frame capture, freeze detection, reconnect
    gestures.py                 MediaPipe Tasks, classification, stabilization
    office.py                   foreground detection and STA COM worker
    runtime.py                  camera/inference/policy orchestration and watchdog
    startup.py                  optional per-user Windows Run registration
    ui.py                       camera canvas, compact settings, enrollment overlays, protected actions, tray
    camera_view.py              aspect-preserving video painter and fading action notifications
  models/                       pinned manifest, official models and license texts
  assets/shield.svg
  tests/                        mocked safety tests plus model/DPAPI/GUI smoke checks
  scripts/                      startup, model setup, packaging
  docs/                         architecture, verification, manual QA, troubleshooting
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
