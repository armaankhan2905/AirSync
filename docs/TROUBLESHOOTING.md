# Troubleshooting

## Camera off or busy

Grant explicit VisionGuard camera consent in first-launch setup, then enable Windows desktop camera access. Close Teams, Zoom, browser capture or other camera owners. Save a different camera index in Camera Settings. The application intentionally does not probe other cameras without your selection. A disconnected camera retries automatically. A black/near-uniform frame is reported as blocked/dark; it is never verified. An exact unchanged feed for more than three seconds is treated as frozen. Perfectly static cameras can produce false freeze alerts; verify this with your device.

## Inference stopped or missing models

Run `scripts/fetch_models.py` with the environment's Python, then `scripts/fetch_models.py --verify-only`. Use Dashboard → Start / restart camera & inference; an existing worker restart requires the recovery passphrase. Check Activity Logs → Refresh diagnostic log for details. If a camera driver remains stuck in a native read, close the application through its protected exit when possible, resolve the driver/device problem, and relaunch. Do not assume a failed worker is monitoring successfully. Auto-lock's camera-failure policy continues through the GUI watchdog.

## Environment errors

Use 64-bit CPython and an isolated `.venv`. Do not mix Anaconda's global dependencies into it. Only `opencv-contrib-python==4.12.0.88` should be installed; remove both OpenCV distributions and reinstall requirements if necessary. `pip check` should report no broken requirements. Install the current Microsoft Visual C++ runtime if Qt/OpenCV/MediaPipe DLL loading fails. No network is used by the running application.

## Face never verifies

Enable Dashboard → Developer Diagnostics. Compare the current similarity with the acquisition/retention thresholds; distinguish absent, poor quality, unknown and recognition errors. The default acquire cutoff remains 0.53, retention 0.50, with three consecutive observations and 1.2-second benign-dropout grace. Continuous fresh owner matches renew completed liveness; a fresh challenge every minute is no longer expected. Face inference older than 800 ms is discarded. A sustained model error is reported separately from a camera error; the next frame retries. Do not change thresholds merely to hide an error.

Use even frontal illumination and move closer until the face box is at least 90 pixels across. Avoid blur, face coverings and extreme yaw. Complete prompts from the mirrored-preview perspective. Enrollment requires samples from both turned poses and center. Wait for three fresh matched/liveness-approved observations. Make sure there is only one face during the challenge. Use an absence timeout long enough to complete liveness on your CPU. Do not lower recognition sensitivity simply to make every face pass; test an unauthorized person after adjustment. Real accuracy across demographic groups and environments has not been benchmarked.

## Gesture visible but no Office action

For PowerPoint, opening the file in the editor is not enough: press F5 and focus the slideshow. After verification, show an open palm for about a second, then form an L with your thumb and index finger extended and the other fingers folded. Hold for about a second. The default right L advances and left L goes back. Open your palm again between actions and after verification or focus changes. The dashboard's Gesture Control card now states whether it is waiting for face verification, a slideshow, or an open palm.

Check VERIFIED, unlocked session, global gestures enabled, exactly one face, and a foreground supported Office window. Open your hand first; keep the palm visible for three neutral observations. Then hold an L or exit gesture steadily. Returning a hand after it leaves view does not rearm it. Check handedness labels and Swap calibration. A higher confidence threshold than a gesture's rule score can intentionally disable that gesture. Word gesture pause is resumed with a mapped toggle (default pinch); global gesture disable must be reenabled through the UI/tray. Hand labels in the preview are live detections, not confirmation that a command fired.

## Office unavailable

The dashboard can now show a running PowerPoint editor even while VisionGuard has focus. Inspect PowerPoint/Word running flags, installed flags, COM status, foreground executable and slideshow count in Developer Diagnostics. **PowerPoint Running — No Active Slideshow** means the process was detected; press F5 to enable slideshow navigation. A single slideshow on another monitor can be targeted from a foreground PowerPoint presenter/editor window; an unrelated foreground process still blocks actions. If more than one slideshow exists, focus the intended slideshow window.

Install and start the Windows desktop version of Word/PowerPoint. Run it at the same privilege level as VisionGuard. Start and focus the slideshow display rather than a PowerPoint editing window or unrelated Presenter View window. The app cancels commands on focus change or missing slideshow. In Word choose Print Layout for exact page scrolling. Read Mode uses screen scrolling. Draft/Outline views do not receive page commands. Dismiss modal Office dialogs and wait for COM to recover. Multiple Office instances and Presenter View can prevent conservative foreground matching; no keyboard fallback is injected.

## Locked or simulated lock pending

The dashboard banner explicitly says AUTO-LOCK OFF when presence locking is disabled. To enable real locking, launch without `--simulation`, open Security settings, enable presence-based auto-lock, uncheck Development simulation, and save using the application recovery passphrase and the real-lock confirmation. This locks Windows; it does not power off the laptop. A forced simulation launch shows a disabled simulation checkbox with restart instructions.

Unlock with Windows, never VisionGuard. A real session transition resets the application's challenge and policy. A **simulated** lock is deliberately latched: use authorized pause/resume or dashboard restart to run another simulation. The forced `--simulation` command-line option prevents enabling real locking in that run. Launch without it and use protected settings plus explicit confirmation to test real locks. Auto-lock is initially disabled.

## Corrupted protected data or lost recovery passphrase

No automatic reset or plaintext fallback is provided. Restore the intact application database and original Windows profile from your own backup. A DPAPI blob usually cannot be decrypted under a different account/machine or after losing the account's DPAPI keys. If no backup or recovery passphrase is available, use normal Windows account control to stop/uninstall this supplementary desktop application and set it up again; enrollment must be recreated. This is an account-owner recovery procedure outside VisionGuard's protected UI, not an in-app administrative bypass. Configuration with enrollment but missing authorization is rejected. Camera consent must be given again for a new profile.

## Startup, tray and uninstall

Look for the VisionGuard shield in the Windows tray overflow area. Closing minimizes by default. Use tray → Exit and the application recovery passphrase. Disable Start with Windows before deleting the portable installation folder; Windows Startup Apps can also disable it. The registry value is `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VisionGuardAI`. Delete biometric enrollment using the protected page. After exiting, the account owner can remove `%LOCALAPPDATA%\VisionGuardAI` to remove settings, hashes and diagnostic logs as well. Backups may retain earlier copies. A development `.venv` and downloaded models belong to the project directory and can be removed separately.

## Diagnostic location

`%LOCALAPPDATA%\VisionGuardAI\visionguard.log` rotates at approximately 1 MB with three backups. Logs contain error stacks and state/operation diagnostics, not facial vectors, frames, recovery passphrases or document content. The GUI retains a bounded recent activity list in memory. Report errors after checking logs for local path information you do not want to share.
