# -*- mode: python ; coding: utf-8 -*-
"""cortex 打包配置 — 单 exe（桌面客户端 + 内置后端）

桌面客户端（PyQt6 + QtWebEngine）启动时在后台线程内置启动后端 API
（见 frontend/main.py _start_backend_thread），不再生成独立的 AI_Backend。

  - cortex(.exe)   唯一启动文件
    * 启动时后台线程运行 uvicorn（api.main:app）
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

# ── Embedding 模型（打入 exe，避免冷启动联网下载；见 main.py _setup_runtime_data）──
_embed_models = os.path.join(ROOT, "data", "memory", "embeddings", "models")
if os.path.isdir(_embed_models):
    datas.append((_embed_models, "data/memory/embeddings/models"))
    print(f"[spec] 打包 Embedding 模型: {_embed_models}")

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

# ── 桌面客户端（入口 = frontend/main.py，内置后端线程）──
exe_client = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='cortex',
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
    icon=os.path.join(ROOT, 'frontend/public/cortex.icns') if os.path.exists(
        os.path.join(ROOT, 'frontend/public/cortex.icns')) else None,
)

# ── macOS 应用包（.app，可拖入 /Applications，双击如正常 App 启动，无终端）──
# onedir 模式：exe 依赖由 BUNDLE 收集到 Contents/Frameworks
app = BUNDLE(
    exe_client,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='cortex.app',
    icon=os.path.join(ROOT, 'frontend/public/cortex.icns') if os.path.exists(
        os.path.join(ROOT, 'frontend/public/cortex.icns')) else None,
    bundle_identifier='com.cortex.app',
    info_plist={
        'CFBundleName': 'cortex',
        'CFBundleDisplayName': 'cortex',
        'CFBundleShortVersionString': '0.0.1',
        'CFBundleVersion': '0.0.1',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSPrincipalClass': 'NSApplication',
    },
)


# ── 打包后修复：QtWebEngine macOS framework ──────────────────────
# PyInstaller 收集 PyQt6 的 QtWebEngineCore.framework 时，Helper 可执行程序与资源
# 未按 macOS framework 的标准布局放置，导致运行时
#   (1) 找不到 QtWebEngineProcess / qtwebengine 资源（黑色界面/白屏）
#   (2) QtWebEngineProcess 的 @rpath 无法解析到 QtWebEngineCore.framework（dyld 崩溃）
# 这里在打包完成后自动修复布局，并把修复固化，避免每次手动改产物。
def _post_fix_qtwebengine(outdir):
    fw = None
    # 兼容两种布局：COLLECT(onedir: _internal) 与 BUNDLE(.app: Contents/Frameworks)
    for rel in ("_internal/PyQt6/Qt6/lib/QtWebEngineCore.framework",
                "Contents/Frameworks/PyQt6/Qt6/lib/QtWebEngineCore.framework"):
        cand = os.path.join(outdir, rel)
        if os.path.isdir(cand):
            fw = cand
            break
    if fw is None:
        print(f"[spec] QtWebEngineCore.framework 未找到，跳过 WebEngine 修复: {outdir}")
        return

    src_fw = None
    try:
        import PyQt6.Qt6  # noqa: F401
        from PyInstaller.utils.hooks.qt import pyqt6_library_info
        loc = pyqt6_library_info.location["LibrariesPath"]
        cand = os.path.join(loc, "QtWebEngineCore.framework")
        if os.path.isdir(cand):
            src_fw = cand
    except Exception:
        pass
    if not src_fw:
        print("[spec] 未定位源 QtWebEngineCore.framework，跳过资源复制", flush=True)

    # 1) 确保 Versions/A 下有 Helpers 与 Resources（symlink Helpers/Resources 指向这里）
    import shutil
    va = os.path.join(fw, "Versions", "A")
    for sub in ("Helpers", "Resources"):
        dst = os.path.join(va, sub)
        src = os.path.join(src_fw, sub) if src_fw else None
        if not os.path.isdir(dst) and src and os.path.isdir(src):
            shutil.copytree(src, dst)
            print(f"[spec] 已复制 QtWebEngineCore {sub} -> {dst}")
        elif os.path.isdir(dst) and src and os.path.isdir(src):
            # 已存在：补全缺失资源（framework 收集可能只留了部分文件）
            for name in os.listdir(src):
                s = os.path.join(src, name)
                d = os.path.join(dst, name)
                if os.path.isdir(s):
                    if not os.path.isdir(d):
                        shutil.copytree(s, d)
                elif not os.path.exists(d):
                    shutil.copy2(s, d)

    # 2) 给 QtWebEngineProcess 追加 rpath，使 @rpath/QtWebEngineCore.framework 可解析
    proc = os.path.join(
        fw, "Versions", "A", "Helpers", "QtWebEngineProcess.app", "Contents", "MacOS", "QtWebEngineProcess"
    )
    if os.path.isfile(proc):
        need = "@loader_path/../../../../../../.."  # MacOS -> Qt6/lib
        import subprocess
        rpaths = subprocess.check_output(
            ["otool", "-l", proc], text=True, errors="replace"
        )
        if need not in rpaths:
            subprocess.call(["install_name_tool", "-add_rpath", need, proc])
            print(f"[spec] 已给 QtWebEngineProcess 添加 rpath {need}")
        else:
            print("[spec] QtWebEngineProcess rpath 已存在")

    # 3) 重新签名（关键！）：install_name_tool 会破坏代码签名，未重新签名时
    #    macOS 以 "SIGKILL (Code Signature Invalid)" 杀掉渲染进程 → 黑屏。
    #    必须对 QtWebEngineProcess.app 重新 adhoc 签名，并连同 framework 一起。
    proc_app = os.path.join(fw, "Versions", "A", "Helpers", "QtWebEngineProcess.app")
    if os.path.isdir(proc_app):
        subprocess.call(["codesign", "--force", "--deep", "--sign", "-", proc_app])
        print("[spec] 已重新签名 QtWebEngineProcess.app")
        if os.path.isdir(fw):
            subprocess.call(["codesign", "--force", "--deep", "--sign", "-", fw])
            print("[spec] 已重新签名 QtWebEngineCore.framework")


# 尝试修复 .app（BUNDLE）或 onedir（COLLECT）产物
for _out in (os.path.join(ROOT, "dist", "cortex.app"), os.path.join(ROOT, "dist", "cortex")):
    if os.path.isdir(_out):
        _post_fix_qtwebengine(_out)
        break
print("[spec] 打包后修复完成")
