# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — POSStart 実行ファイルビルド設定

ビルドコマンド:
  cd build
  pyinstaller posstart.spec --clean
"""
import os

APP_NAME = 'POSStart'
APP_VERSION = '1.0.0'

# プロジェクトルート（buildディレクトリの親）
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# 同梱するPythonファイル
hidden_imports = [
    'pos',
    'stripe_service',
    'cast_salary',
    'bottle_keep',
    'customer_crm',
    'closing',
    'management',
    'tab_management',
    'pricing_engine',
    'weather_service',
    'point_mail',
    'backup_service',
    'data_import',
    'db_shared',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'pydantic',
    'sqlalchemy',
    'sqlalchemy.dialects.sqlite',
    'stripe',
    'openpyxl',
    'dotenv',
    'httpx',
]

# 同梱するデータファイル
datas = [
    (os.path.join(ROOT, '.env.example'), '.'),
]

a = Analysis(
    [os.path.join('launcher.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,    # コンソールあり（停止できるように）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, 'icon.ico') if os.path.exists(os.path.join(SPECPATH, 'icon.ico')) else None,
    version=os.path.join(SPECPATH, 'version_info.txt') if os.path.exists(os.path.join(SPECPATH, 'version_info.txt')) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
