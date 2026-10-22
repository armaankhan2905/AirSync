# Persistent users and automatic authorization

## Using the feature

The camera screen has a **+ Register User** button beside Settings. Registration and **Settings → Manage Users** require the local administrator recovery passphrase. This is an application credential, never a Windows password.

On a fresh profile, grant camera consent and create that credential. Register the first person's name and face; this account becomes the administrator and device owner. Capture uses the existing camera and inference workers, requires one visible face, quality samples from three poses, and a randomized head-turn challenge. Progress and errors appear inside the compact registration dialog. No photographs are saved.

On an existing profile, the original encrypted enrollment migrates transactionally into an administrator named **Device Owner**, with workstation-presence trust. The existing recovery credential and application settings remain valid. Rename that account through Manage Users; no re-enrollment is needed for migration. A failed migration rolls back rather than discarding enrollment.

Future launches load the stored templates automatically. There is no Verify button and no routine re-registration. A fresh randomized liveness challenge and consecutive matching observations are still required after startup or a meaningful interruption. Continuous fresh matches renew liveness. The top camera overlay displays the recognized name; unregistered, uncertain, absent, camera-error and multiple-person states remain distinct.

Registration remains in `%LOCALAPPDATA%\VisionGuardAI\visionguard.sqlite3` across application restarts and Windows reboots, unless explicitly deleted. Launching with a different `--data-dir` selects a different profile.

## Permissions and workstation presence

- The first administrator/device owner has both Office permissions, gesture permission, user-management permission and trusted workstation presence.
- Additional Authorized Users can control PowerPoint and Word by default, but receive neither management permission nor workstation-presence trust. Additional administrators do not automatically receive presence trust either.
- Manage Users can change names, roles and application permissions; enable/disable users; delete users; re-enroll a selected face; explicitly grant trusted presence; or transfer device ownership.
- Every management mutation validates a short-lived, in-memory administrative capability. Capabilities expire after five minutes, are revoked when the recovery credential changes, and are not persisted across restarts. Named delegates additionally need active user-management permission. The local recovery credential remains the administrative recovery path when no camera identity is available.
- At least one active administrator is retained. Transfer device ownership before disabling or deleting the owner. A device owner must remain presence-trusted. A transfer retains the previous owner's existing trust until an administrator explicitly removes it.
- Exactly one visible, verified and permitted person is required for any gesture command. Two registered people, or an owner plus an unknown person, suspend all gesture commands. Facial matching and presence monitoring continue.

**Office authorization and workstation presence are separate.** A verified presenter can control permitted Office applications while the owner's absence timer continues. Only the owner or an explicitly trusted account satisfies presence. When locking is pending, Office commands are denied. Strict privacy still treats an unknown additional face as a privacy violation; a known registered guest does not become an unknown face merely because they lack presence trust. Windows unlocking is never automated.

PowerPoint retains left/right L navigation and fist/pinch slideshow exit. Word retains page navigation, fist pause and pinch pause/resume. Global gesture enablement, neutral rearming, stabilization, foreground-window matching, stale-command expiration and COM guards remain in force.

## Storage and recognition implementation

SQLite uses foreign keys, indexed user templates, a directory revision, user metadata/permissions and a bounded 2,000-event audit history. Templates are Windows DPAPI-protected per sample and bind the embedding to its application user ID and SFace model version inside the encrypted payload. Protected policy copies bind names, roles, activity, ownership and permissions to each user. Direct metadata tampering, cross-user template reassignment, missing samples, unsupported models, invalid vectors and unreadable ciphertext fail closed.

Disabled accounts retain registration for re-enabling but cannot match for access. Duplicate detection includes disabled registrations. Re-enrollment excludes the selected account from duplicate checks but cannot replace another account's face. Deletion cascades to templates and permissions, nulls historical audit references, uses SQLite secure deletion and compacts the database. External backups or storage snapshots may retain previously encrypted data.

The existing YuNet/SFace preprocessing and unit normalization are shared between capture and recognition. Matching requires agreement with the closest two samples. Default acquisition remains 0.53; the 0.50 retention cutoff applies only to the same previously verified identity. Competing registered identities within a 0.05 score gap are uncertain and cannot authorize. Spatial tracks retain separate liveness challenges; a track changing identity cannot borrow the previous person's liveness. Identity verification still requires the configured number of fresh consecutive observations.

Directory revisions revoke cached access immediately. The Office dispatcher checks current application permission, registered user ID, authentication epoch, current session, focus and command freshness; its final COM guard repeats permission and epoch checks. A disabled/deleted user cannot retain access until the next camera frame or GUI refresh. A COM action already executing cannot be undone by a later permission change.

The source files changed for this feature are:

- New: `visionguard/users.py`, `visionguard/recognition.py`, `visionguard/user_dialogs.py`.
- Integration: `visionguard/storage.py`, `visionguard/authentication.py`, `visionguard/state.py`, `visionguard/runtime.py`, `visionguard/office.py`, `visionguard/ui.py`, `main.py`.
- Tests: `tests/test_users.py`, `tests/test_multiuser_authorization.py`, `tests/test_user_dialogs.py`, updated `tests/test_camera_interface.py`.
- Documentation: this guide, README, the verification record and three UI render fixtures.

## Verification and limits

The full suite passes **236 tests**, including the prior 176 checks. New coverage includes administrator capabilities, duplicates, persistence/reopen, automatic matching after restart, uncertain/competing identities, same-user hysteresis, per-face tracks, unknown rejection, liveness, single-person gesture enforcement, Office-specific permissions, permission revocation during COM lookup, owner/presenter separation, trusted-presence opt-in, migration rollback, encrypted template identity binding, corruption, disabling/deletion, dialog workflows and stale registration results. A test-only registration also roundtrips through real Windows DPAPI and SQLite reopening. Camera and Office positive-operation tests use mocks.

Offscreen main-window, registration, management and minimum-size layouts were rendered and inspected. The images use synthetic camera frames and fabricated test identities: [camera](camera-multiuser.png), [registration](user-registration.png), [management](manage-users.png). They are not evidence of physical enrollment or Office execution.

**RGB liveness is not certified presentation-attack detection.** A stationary matched face alone cannot complete the randomized challenge, but a printed image moved convincingly, a prerecorded sequence, or manipulated video can still spoof basic webcam head-pose checks. The app cannot guarantee rejection of all photographs or recorded videos. Strong replay resistance requires separately validated anti-spoofing hardware/models. Similarity thresholds and the competitor margin are conservative heuristics, not measured false-acceptance guarantees. Same-Windows-account malware can still interfere with this desktop application or use that account's DPAPI access.

Physical multi-user registration, actual webcam recognition accuracy, photo/video spoof resistance, Office execution, reboot/startup, sustained CPU behavior and real workstation locking remain manual checks. Do not mark those passed solely because the automated suite passes. The existing packaged executable has not been rebuilt.

## Windows webcam and Office manual checks

Use the normal profile to inspect migration, or a separate profile to test first setup:

```powershell
.\.venv\Scripts\python.exe main.py --simulation --data-dir .tmp\users-manual-profile
```

1. **Setup/administrator approval.** Decline consent once, then complete setup. Try a wrong recovery credential. No user should be added without successful administrative authorization. Register one administrator; inspect progress, blur/lighting feedback and cancel/retry behavior.
2. **Duplicate/new registration.** Retry the same face under a different name: expect `User already registered.` Register a second consenting person. Confirm their default role is Authorized User with no trusted presence.
3. **Persistence and automatic return.** Use protected Exit and relaunch the same profile. Both names should remain in Manage Users. Let each person appear alone and complete the fresh automatic challenge; expect the correct name and VERIFIED without a Verify button or re-enrollment. Repeat after a Windows reboot.
4. **Unknown/uncertain identity.** Test a consenting unregistered person, off-angle views and insufficient-quality samples. No Office command should execute. Compare recognition against lookalikes and ordinary lighting changes; record failures rather than treating a score as an accuracy guarantee.
5. **PowerPoint.** Focus a real slideshow, show a neutral palm, then each L/fist/pinch gesture. Expect one permitted action per hold and a confirmation only after execution. The editor without an active show must not navigate.
6. **Word and per-app permissions.** Disable Word permission for the presenter, save and verify that Word commands are refused while permitted PowerPoint commands work. Re-enable Word and test navigation/pause/resume in supported views. Unknown users must control neither app.
7. **Owner versus presenter.** Save documents, retain Windows credentials, choose a timeout long enough for liveness, and enable simulated auto-lock. Verify the owner, then have only the presenter remain. Their verified gesture identity must not reset the owner's absence timer. Observe one simulated lock. Reset through protected monitoring controls, explicitly grant presenter presence trust, and repeat.
8. **Multiple people.** Test two registered people, owner plus unknown, and presenter plus unknown. Gestures must pause for all combinations. Check relaxed and strict privacy: an unknown additional face can start the strict absence policy; a registered guest must not be mislabeled unknown.
9. **Revocation/deletion/re-enrollment.** Disable a currently verified presenter while they hold a gesture; no subsequent command should execute. Re-enable, alter application permission, delete, and re-enroll with authorization. Verify templates/permissions disappear on deletion and another person's cached identity is not reused. Owner deletion requires an explicit ownership transfer.
10. **Background, failures and real locking.** Minimize and focus Office; processing must continue without focus stealing. Test camera disconnect/reconnect and session lock/unlock. Follow M08–M17 in MANUAL_TESTS.md before separately enabling real locking; simulation never calls LockWorkStation. Check startup/reboot and camera/CPU responsiveness on the target laptop.
11. **Spoof assessment.** With permission, test printed photographs and recorded video; record the actual outcome. Any successful spoof invalidates a claim of secure anti-spoofing. Do not deploy the RGB check as a Windows-authentication replacement.


## Local deployment check

The existing normal profile was migrated and the updated source launched in forced simulation mode. A read-only metadata check found one active administrator/device owner, 12 encrypted templates, no remaining legacy enrollment row and zero foreign-key violations. The application was responsive and camera acquisition started. This confirms migration and startup only; it does not mark new physical registration, recognition accuracy, spoof resistance or Office command execution as passed.
