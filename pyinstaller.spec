# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# 收集所有数据文件
datas = [
    # 配置文件
    ('config/*.py', 'config'),
    ('config/*.json', 'config'),
    ('config/*.yaml', 'config'),
    
    # 记忆文件
    ('data/memory/*.json', 'data/memory'),
    ('data/memory/**/*.json', 'data/memory'),
    
    # 提示词
    ('modules/thinking/evolution/prompts/*.txt', 'modules/thinking/evolution/prompts'),
    
    # SQLite 数据库（如果使用）
    ('data/*.db', 'data'),
]

# 收集隐藏导入
hiddenimports = [
    # 数据库
    'sqlalchemy',
    'sqlalchemy.orm',
    'sqlalchemy.ext.declarative',
    'sqlalchemy.dialects.postgresql',
    'sqlalchemy.dialects.sqlite',
    'psycopg2',
    'redis',
    
    # Web框架
    'fastapi',
    'uvicorn',
    'starlette',
    
    # ML
    'transformers',
    'torch',
    
    # 其他
    'pydantic',
    'pydantic_settings',
    'numpy',
    'PIL',
    'PIL._imaging',
    'click',
    'httpx',
    'anyio',
]

# 收集二进制文件
binaries = []

# Windows 特定
if sys.platform == 'win32':
    datas.append(('C:/Windows/System32/msvcp140.dll', '.'))
    datas.append(('C:/Windows/System32/vcruntime140.dll', '.'))
    datas.append(('C:/Windows/System32/vcruntime140_1.dll', '.'))

a = Analysis(
    ['main.py'],
    pathex=[".", "./modules", "./config", "./utils"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'test',
        'pytest',
        '_pytest',
        'py.test',
    ],
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
    name='AI_Backend',
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
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AI_Backend',
)

# 单文件模式（较大但更方便）
onefile = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=True,
    name='AI_Backend_Single',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
)
