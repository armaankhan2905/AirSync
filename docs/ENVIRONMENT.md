# Development environment inspection

Inspection date: **21 October 2026 (Asia/Calcutta)**.

- The AirSync workspace started empty; no repository or existing application to preserve and no applicable AGENTS.md was found in the workspace/checked parents.
- OS reported `Windows-11-10.0.26300-SP0` by Python. CIM system inventory was restricted by the execution sandbox.
- Default global Python was Anaconda Python 3.13.5 with an existing mix of packages. An isolated environment was created from installed CPython **3.13.3 x64** at `.venv`; it does not inherit global site packages.
- Pinned runtime versions actually imported: OpenCV **4.12.0**, MediaPipe **0.10.32**, PySide6 **6.9.3**, NumPy **2.2.6**. `requirements-lock.txt` records exact installed distribution versions, including build/test dependencies.
- `pip check`: no broken requirements. Only `opencv-contrib-python` is installed in the final environment, avoiding the overlapping `opencv-python` distribution.
- Word and PowerPoint desktop executable registrations were present under Microsoft's Office16 installation directory. This confirms installation registration only; no actual Office command or presentation/document operation was performed.
- Camera device inventory could not be established through the restricted device query. No webcam was opened during implementation; real availability and Windows permission must be checked through the consent-based app setup/manual checklist.
- Official YuNet, SFace and MediaPipe hand models were downloaded with their upstream license texts. The OpenCV Zoo revision and all model SHA-256 hashes are recorded in `models/manifest.json` and checked offline before inference.
- Network access was used for dependency/model setup and official documentation only. Core inference and application operation have no network dependency.
- Actual Windows DPAPI encryption/decryption was exercised on test-only data. No Windows lock, Windows credential handling, startup registration, or live Office automation was executed during automated checks.
- Offscreen GUI rendering required explicitly loading the installed Segoe UI font because Qt's sandboxed offscreen plugin could not enumerate system fonts. The font is read from Windows, not redistributed.

See `VERIFICATION.md`, the JSON feature matrix and `MANUAL_TESTS.md` for scope and remaining verification.
