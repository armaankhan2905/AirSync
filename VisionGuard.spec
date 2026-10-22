# One-folder distribution preserves Qt LGPL replaceable shared libraries.
from PyInstaller.utils.hooks import collect_all
import importlib.metadata
from pathlib import Path

license_data = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata['Name']
    for entry in distribution.files or []:
        if any(part.lower().startswith(('license', 'licence', 'copying', 'notice')) for part in entry.parts):
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                license_data.append((str(source), str(Path('licenses') / name / Path(*entry.parts).parent)))

mp_data, mp_bins, mp_hidden = collect_all(
    'mediapipe', filter_submodules=lambda name: not any(
        part in name for part in ('.test', '.benchmark', '.genai', '.metadata.metadata_writers')))
a = Analysis(
    ['main.py'], pathex=[],
    binaries=mp_bins,
    datas=mp_data + license_data + [('assets', 'assets'), ('models', 'models'),
                     ('THIRD_PARTY_NOTICES.md', '.'), ('docs', 'docs'),
                     ('README.md', '.')],
    hiddenimports=mp_hidden + ['win32crypt', 'win32timezone', 'pythoncom',
                                'win32com.client', 'win32gui', 'win32api'],
    hookspath=[], runtime_hooks=[], excludes=['pytest', 'tkinter', 'IPython', 'notebook'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VisionGuardAI',
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=False, console=False, disable_windowed_traceback=False, icon='assets/shield.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VisionGuardAI')
