# PyInstaller 6 one-folder bundle. No private certificates or unused font files.
from PyInstaller.utils.hooks import collect_submodules
import os
import sys
from pathlib import Path

# Freeze only this Python/Qt runtime. Unrelated tools on PATH can supply an
# incompatible icuuc.dll or OpenSSL DLL with the same filename.
if sys.platform == 'win32':
    windows = Path(os.environ.get('WINDIR', 'C:/Windows'))
    os.environ['PATH'] = os.pathsep.join(str(path) for path in (
        Path(sys.prefix) / 'Scripts', Path(sys.base_prefix),
        Path(sys.base_prefix) / 'DLLs', windows / 'System32', windows,
    ))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('resources/icons', 'resources/icons'),
           ('resources/styles', 'resources/styles'),
           ('resources/sounds', 'resources/sounds')],
    hiddenimports=collect_submodules('meshtastic.protobuf') + ['meshcore'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MIM',
          debug=False, strip=False, upx=False, console=os.environ.get('MIM_BUILD_CONSOLE') == '1',
          icon='resources/icons/mim.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MIM')
