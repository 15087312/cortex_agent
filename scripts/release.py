"""发版辅助：统一更新 VERSION 与 frontend/package.json 的版本号，可选打 tag 推送。

用法:
  python scripts/release.py patch                # 2.0.0 -> 2.0.1
  python scripts/release.py minor                # 2.0.1 -> 2.1.0
  python scripts/release.py major                # 2.1.0 -> 3.0.0
  python scripts/release.py 2.3.4                # 指定版本
  python scripts/release.py patch --tag          # 升级 + 创建并推送 git tag v2.0.1
  python scripts/release.py patch --tag --push   # 同上，推送 tag

发布流程（升级后打 tag，GitHub Actions 的 release.yml 会自动构建并上传 Release）:
  git tag v2.0.1 && git push origin v2.0.1
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")
PKG_JSON = os.path.join(ROOT, "frontend", "package.json")


def read_version() -> str:
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_version(v: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(v + "\n")
    with open(PKG_JSON, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    pkg["version"] = v
    with open(PKG_JSON, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bump(current: str, kind: str) -> str:
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if kind == "major":
        parts[0] += 1
        parts[1] = parts[2] = 0
    elif kind == "minor":
        parts[1] += 1
        parts[2] = 0
    else:  # patch
        parts[2] += 1
    return ".".join(str(x) for x in parts[:3])


def main() -> int:
    ap = argparse.ArgumentParser(description="统一升级版本号并打 tag")
    ap.add_argument("version_or_kind", nargs="?",
                    help="版本号 或 major/minor/patch（默认 patch）")
    ap.add_argument("--tag", action="store_true", help="创建 git tag v<版本>")
    ap.add_argument("--push", action="store_true", help="推送 tag 到 origin（隐含 --tag）")
    args = ap.parse_args()

    current = read_version()
    target = args.version_or_kind or "patch"
    if re.fullmatch(r"\d+\.\d+\.\d+", target):
        new = target
    elif target in ("major", "minor", "patch"):
        new = bump(current, target)
    else:
        print(f"[ERR] 无效版本: {target}（应为 x.y.z 或 major/minor/patch）")
        return 1

    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        print(f"[ERR] 无效版本号: {new}")
        return 1

    print(f"[..] {current} -> {new}")
    write_version(new)
    print(f"[OK] 已更新 VERSION 与 frontend/package.json")

    if args.push or args.tag:
        tag = f"v{new}"
        print(f"[..] 创建 tag {tag} ...")
        subprocess.run(["git", "add", "VERSION", "frontend/package.json"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m", f"release: v{new}"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "tag", tag], cwd=ROOT, check=True)
        print(f"[OK] 已提交并创建 tag {tag}")
        if args.push:
            subprocess.run(["git", "push", "origin", "main", tag],
                           cwd=ROOT, check=True)
            print(f"[OK] 已推送 main 与 {tag}")
    else:
        print("下一步：提交改动并打 tag")
        print(f"  git add VERSION frontend/package.json && git commit -m 'release: v{new}'")
        print(f"  git tag v{new} && git push origin v{new}")
        print("（tag 推送后 GitHub Actions 会自动构建并上传 Release）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
