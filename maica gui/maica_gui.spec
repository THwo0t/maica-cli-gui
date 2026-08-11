# -*- mode: python ; coding: utf-8 -*-
#
# Advanced single-spec build entrypoint. The recommended Windows build path is
# the root `build_gui_exe.ps1`, which builds GUI and embedding service as two
# independent onedir targets and then copies the service exe into the GUI dist.

from pathlib import Path


ROOT = Path.cwd()
GUI_DIR = ROOT / 'maica gui'
CLI_DIR = ROOT / 'maica cli'
ASSET_RUNTIME_DIR = ROOT / 'maica gui assets' / 'runtime'
LIVE2D_WEB_DIR = GUI_DIR / 'live2d_web' / 'dist'

CLI_EXCLUDES = [
    'config.json',
    'maica_cli.db',
    '*.db',
    'logs',
    '__pycache__',
    'data/*.faiss',
    'data/*_meta.jsonl',
]


def is_excluded(path, root, patterns):
    rel = path.relative_to(root).as_posix()
    for pattern in patterns:
        if path.match(pattern) or rel == pattern or rel.startswith(pattern.rstrip('/') + '/'):
            return True
    return False


def collect_data_files(root, prefix, excludes=None):
    excludes = excludes or []
    rows = []
    for path in root.rglob('*'):
        if not path.is_file() or is_excluded(path, root, excludes):
            continue
        target_dir = Path(prefix) / path.relative_to(root).parent
        rows.append((str(path), str(target_dir)))
    return rows


datas = collect_data_files(CLI_DIR, 'maica cli', CLI_EXCLUDES)
datas += collect_data_files(ASSET_RUNTIME_DIR, 'maica gui assets/runtime')
if LIVE2D_WEB_DIR.exists():
    datas += collect_data_files(LIVE2D_WEB_DIR, 'maica gui/live2d_web/dist')

block_cipher = None

a = Analysis(
    [str(GUI_DIR / 'gui_app.py')],
    pathex=[str(GUI_DIR), str(CLI_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='maica-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

service_a = Analysis(
    [str(CLI_DIR / 'embedding_service.py')],
    pathex=[str(CLI_DIR)],
    binaries=[],
    datas=collect_data_files(CLI_DIR, 'maica cli', CLI_EXCLUDES),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
service_pyz = PYZ(service_a.pure, service_a.zipped_data, cipher=block_cipher)
service_exe = EXE(
    service_pyz,
    service_a.scripts,
    [],
    exclude_binaries=True,
    name='maica-embedding-service',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    service_exe,
    a.binaries,
    service_a.binaries,
    a.zipfiles,
    service_a.zipfiles,
    a.datas,
    service_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='maica-gui',
)
