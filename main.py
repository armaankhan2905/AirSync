"""VisionGuard AI desktop entry point. No camera or automation on import."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="VisionGuard AI supplementary presence security")
    parser.add_argument("--simulation", action="store_true", help="Force lock simulation for this run")
    parser.add_argument("--background", action="store_true", help="Launch in system tray after consent and enrollment")
    parser.add_argument("--data-dir", type=Path, help="Separate application profile directory")
    parser.add_argument("--smoke-test", action="store_true", help="Render GUI without camera, Office, startup or locking")
    parser.add_argument("--screenshot", type=Path, help="Save GUI image during --smoke-test")
    parser.add_argument("--self-test", type=Path, metavar="REPORT.json", help="Run offline model and DPAPI diagnostics; no camera, Office commands or locking")
    args = parser.parse_args()
    if args.self_test:
        from visionguard.diagnostics import self_test
        return self_test(args.self_test)
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFontDatabase
    from visionguard.config import data_dir
    from visionguard.storage import Database
    from visionguard.ui import MainWindow, STYLE

    app = QApplication(sys.argv[:1])
    # The offscreen Windows plugin cannot enumerate installed fonts in some
    # sandboxes. Load the OS font explicitly for readable smoke-test renders.
    if args.smoke_test and sys.platform == "win32":
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"))
    app.setApplicationName("VisionGuard AI")
    app.setOrganizationName("VisionGuardAI")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    directory = args.data_dir or data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "instance.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "VisionGuard AI", "VisionGuard is already running for this profile. Open it from the tray.")
        return 1
    handler = RotatingFileHandler(directory / "visionguard.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        database = Database(directory / "visionguard.sqlite3")
        database.log_path = directory / "visionguard.log"
        database.users.migrate_legacy()
        settings = database.settings()
        # Force validation on startup, never silently overwrite corrupted biometrics.
        database.embeddings()
        database.users.templates(include_disabled=True)
        if (len(database.embeddings()) or database.users.has_users()) and database.authorization_record() is None:
            raise RuntimeError("Enrolled profile is missing its authorization record; restore your backup")
        window = MainWindow(database, settings, smoke=args.smoke_test, background=args.background,
                            force_simulation=args.simulation or args.smoke_test)
        window.show()
        if args.smoke_test:
            def finish():
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(args.screenshot))
                window.exiting = True
                database.close()
                app.quit()
            QTimer.singleShot(500, finish)
        return app.exec()
    except Exception as exc:
        logging.exception("Startup failed")
        if args.smoke_test:
            print(f"Startup failed: {exc}", file=sys.stderr)
        else:
            QMessageBox.critical(None, "VisionGuard AI — recovery required",
                                 f"Unable to open protected configuration. {exc}\n"
                                 "No data was overwritten. See docs/TROUBLESHOOTING.md.")
        return 1
    finally:
        lock.unlock()
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
