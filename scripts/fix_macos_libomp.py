#!/usr/bin/env python3
"""
macOS 单一 OpenMP 运行时修复 — 合并所有捆绑 libomp 的库到 torch 的 libomp

问题（OMP: Error #15 / 段错误）：
  faiss、sklearn 等库各自捆绑一份 libomp.dylib，与 torch 的 libomp 并存时
  同一进程出现多套 OpenMP 运行时。第二个运行时初始化 abort（OMP: Error #15）；
  用 KMP_DUPLICATE_LIB_OK 容忍后仍可能在 BERT/torch 推理时段错误
  （OpenMP 线程池状态冲突，随机 flaky）。

根因修复：
  用 install_name_tool 把 site-packages 下所有二进制对 libomp.dylib 的
  相对引用（@loader_path/...、@rpath/...）改为 torch 的 libomp 绝对路径，
  使 dyld 按规范化路径去重 → 进程只有一份 OpenMP 运行时。

使用：
  python scripts/fix_macos_libomp.py [--check] [--restore]

  --check   只检测是否已修复（幂等），不修改。
  --restore 从最新备份目录 /tmp/libomp_bak_<ts> 恢复所有二进制。

升级 torch / faiss / sklearn 后需重新执行本脚本。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

BACKUP_ROOT = "/tmp/libomp_bak"
TORCH_OMP_REL = os.path.join("torch", "lib", "libomp.dylib")
# 跳过自身（torch 的 libomp 是目标，不可 change 它自己）
SKIP_DIRS = ("torch",)


def _site_packages() -> str:
    import site
    paths = site.getsitepackages()
    for p in paths:
        if os.path.exists(os.path.join(p, "faiss")) and os.path.exists(os.path.join(p, "torch")):
            return p
    raise SystemExit("未找到同时含 faiss 与 torch 的 site-packages，请确认已安装")


def _torch_libomp(sp: str) -> str:
    p = os.path.join(sp, *TORCH_OMP_REL.split(os.sep))
    if not os.path.exists(p):
        raise SystemExit(f"torch libomp 不存在: {p}")
    return os.path.abspath(p)


def _all_binaries(sp: str):
    """site-packages 下所有 .so / .dylib（排除 torch 目录自身）"""
    for dirpath, dirnames, filenames in os.walk(sp):
        rel = os.path.relpath(dirpath, sp)
        if any(rel == d or rel.startswith(d + os.sep) for d in SKIP_DIRS):
            continue
        for f in filenames:
            if f.endswith((".so", ".dylib")):
                yield os.path.join(dirpath, f)


def _libomp_refs(path: str):
    """返回该二进制对 libomp.dylib 的依赖引用列表"""
    out = subprocess.run(["otool", "-L", path], capture_output=True, text=True).stdout
    return [
        l.strip().split()[0]
        for l in out.splitlines()
        if "libomp" in l and path not in l
    ]


def _scan(sp: str) -> list:
    """返回 [(binary, [refs...])] 需要修复的项"""
    target = _torch_libomp(sp)
    result = []
    for b in _all_binaries(sp):
        # 捆绑的 libomp.dylib 文件本身（被依赖方）不重定向；孤儿后无影响
        if b.endswith((".dylibs", os.sep + "libomp.dylib")) or b.endswith(os.sep + "libomp.dylib"):
            continue
        refs = _libomp_refs(b)
        changed = [r for r in refs if r != target]
        if changed:
            result.append((b, changed))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    sp = _site_packages()
    target = _torch_libomp(sp)
    items = _scan(sp)

    if args.check:
        if items:
            print(f"未完全合并（{len(items)} 个二进制仍依赖其他 libomp）：")
            for b, refs in items[:20]:
                print(f"  {os.path.relpath(b, sp)}: {refs[:1]}")
            return 1
        print("已全部合并到 torch libomp ✓")
        return 0

    if args.restore:
        baks = sorted(
            [d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d))]
        ) if os.path.isdir(BACKUP_ROOT) else []
        if not baks:
            raise SystemExit(f"无备份可用（{BACKUP_ROOT} 为空）")
        src = os.path.join(BACKUP_ROOT, baks[-1])
        for b, _ in items:
            bak = os.path.join(src, os.path.relpath(b, sp).replace(os.sep, "__"))
            if os.path.exists(bak):
                shutil.copy(bak, b)
                _resign(b)
        print(f"已从 {src} 恢复")
        return 0

    if not items:
        print("已全部合并，无需修改 ✓")
        return 0

    backup_dir = os.path.join(BACKUP_ROOT, str(int(time.time())))
    os.makedirs(backup_dir, exist_ok=True)
    for b, _ in items:
        bak = os.path.join(backup_dir, os.path.relpath(b, sp).replace(os.sep, "__"))
        shutil.copy(b, bak)

    for b, refs in items:
        for r in refs:
            subprocess.run(["install_name_tool", "-change", r, target, b], capture_output=True)
        _resign(b)
        print(f"已修复: {os.path.relpath(b, sp)}")

    print(f"\n修复 {len(items)} 个二进制 -> {target}")
    print("备份: " + backup_dir)
    return 0


def _resign(path: str) -> None:
    subprocess.run(["codesign", "--force", "--sign", "-", path], check=False, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
