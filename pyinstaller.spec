# -*- mode: python ; coding: utf-8 -*-
"""Cortex Agent 打包配置 — 一次产出两个可执行文件到 dist/CortexAgent/：

  - AI_Backend(.exe)      后端 API（uvicorn api.main:app），console
  - Cortex_Client(.exe)   桌面客户端（PyQt6 + QtWebEngine），windowed
    * 启动时自动拉起同目录的 AI_Backend（见 frontend/main.py _ensure_backend）
    * 内置前端 server.py（8765）与 Vue 构建产物 frontend/dist

用法: pyinstaller pyinstaller.spec --clean --noconfirm
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

block_cipher = None
ROOT = os.path.abspath(".")

datas = []
binaries = []
hiddenimports = []

# ── 项目代码 ──────────────────────────────────────────────
# uvicorn 用字符串 "api.main:app" 导入，PyInstaller 静态分析发现不了，必须显式收集。
for pkg in ("api", "modules", "config", "utils", "cortex", "infra", "skills"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# ── Web 框架 / 数据库（uvicorn/starlette 同理需显式收集）──
for pkg in ("uvicorn", "fastapi", "starlette", "sqlalchemy", "redis", "httpx", "anyio"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# ── 重量级第三方库：连数据/二进制/子模块一起收集 ──────────
for pkg in (
    "torch",
    "transformers",
    "sentence_transformers",
    "faiss",
    "onnxruntime",
    "whisper",
    "rapidocr_onnxruntime",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        try:
            hiddenimports += collect_submodules(pkg)
        except Exception:
            pass

# ── 前端产物 / 桌宠资源 / 默认配置 ────────────────────────
datas += [
    (os.path.join(ROOT, "frontend/dist"), "frontend/dist"),
    (os.path.join(ROOT, "frontend/public"), "frontend/public"),
    (os.path.join(ROOT, "frontend/pet"), "frontend/pet"),
    (os.path.join(ROOT, "frontend/package.json"), "frontend/package.json"),
    (os.path.join(ROOT, ".env.example"), ".env.example"),
]

# ── Windows VC 运行库 ─────────────────────────────────────
if sys.platform == "win32":
    for dll in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", dll)
        if os.path.exists(p):
            binaries.append((p, "."))

a = Analysis(
    ['frontend/main.py'],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'pytest',
        '_pytest',
        'py.test',
        'IPython',
        'jupyter',
        'notebook',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── 桌面客户端（入口 = frontend/main.py）──
exe_client = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cortex_Client',
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
    icon=os.path.join(ROOT, 'frontend/public/icon.ico') if os.path.exists(
        os.path.join(ROOT, 'frontend/public/icon.ico')) else None,
)

# ── 后端（入口 = packaging/backend_main.py）──
# 第二个 EXE 需带 bootstrap/rthook（a.scripts[:-1]），并以 ('main', src, 'PYSOURCE')
# 作为主脚本（与客户端相同的引导方式），否则 bootloader 无主脚本可跑。
exe_backend = EXE(
    pyz,
    a.scripts[:-1] + [
        ('main', os.path.join(ROOT, 'packaging/backend_main.py'), 'PYSOURCE'),
    ],
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
)

coll = COLLECT(
    exe_client,
    exe_backend,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CortexAgent',
)
