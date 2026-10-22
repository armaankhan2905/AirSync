# Repeated blur rejection — 2026-10-22

## Observed issue

Recent runtime observations often exceeded the unchanged 0.53 identity cutoff while reporting a quality rejection or restarting the head-turn challenge. An in-memory camera diagnostic sampled 24 face crops: focus variance ranged from 32.5 to 85.0, brightness from 105.7 to 110.1, and face widths from 139 to 152 pixels. The original 45-point focus floor rejected five samples. No camera images or embeddings were saved by this diagnostic.

An incomplete liveness challenge also reset on every missing/quality-rejected observation because its completed lease was still zero. Brief blur during the required head movement could therefore erase progress.

## Changes

- Enrollment retains the 45-point focus floor. Live verification uses a separate 30-point floor to tolerate mild softness. Severe blur still fails, as do bad lighting, clipped/small faces, extreme pose and obscured landmarks. Geometric problems now report their specific reason before the blur check.
- Live observations must still match the encrypted owner profile, complete the randomized head-turn challenge and satisfy consecutive verification. Identity similarity thresholds, model preprocessing, storage and workstation-lock policy are unchanged.
- An unfinished challenge preserves completed pose steps through a dropout within its existing grace period. Dropouts never authorize, advance a step or join nonconsecutive pose samples. The original 20-second challenge deadline remains fixed. Sustained loss, unknown identity and multiple faces still reset incomplete progress.
- Enrollment can preserve challenge progress during a short quality dropout, but does not store that rejected sample. Existing sample count, pose coverage and identity checks remain required.
- Runtime logs include quality rejection reasons. The camera UI no longer reports a present owner as absent merely because a head-turn challenge or quality adjustment is in progress.

Modified implementation files: `visionguard/authentication.py`, `visionguard/runtime.py`, `visionguard/ui.py`. New regressions are in `tests/test_authentication.py` and `tests/test_runtime_pipeline.py`.

## Verification

Full automated suite: **176 passed**, including mild-softness separation between enrollment and verification, severe-blur/bad-light rejection, incomplete-challenge dropout recovery, consecutive-pose requirements, fixed challenge deadlines, unknown/multiple-face rejection and unchanged enrollment storage.

The patched source was relaunched in forced simulation mode. Initial live observations entered the head-turn challenge with acceptable quality and owner similarity above the required cutoff. This confirms live startup and quality acceptance; successful end-to-end verification still requires the user to complete the challenge. The focus floors are device-dependent heuristics, not measured biometric accuracy guarantees. The packaged executable has not been rebuilt.
