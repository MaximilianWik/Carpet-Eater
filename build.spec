# PyInstaller spec for Carpet Eater.
# Build: pyinstaller build.spec   (or run build.bat)

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['carpeteater\\__main__.py'],
    pathex=[],
    binaries=[
        ('vendor\\ffmpeg.exe', 'vendor'),
    ],
    datas=[
        ('public\\closed.png', 'public'),
        ('public\\open.png',   'public'),
        ('public\\chew1.png',  'public'),
        ('public\\chew2.png',  'public'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim PySide6 modules we don't use.
        'PySide6.QtNetwork',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia',
        'PySide6.QtPdf',
        'PySide6.Qt3DCore',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtSql',
        'PySide6.QtTest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CarpetEater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='build_icon.ico',
)
