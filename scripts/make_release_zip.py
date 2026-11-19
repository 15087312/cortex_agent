"""把发布产物目录打成 zip（跨平台、Zip64 安全）。

为什么不用现成工具：
  - Windows runner 上的 PowerShell ``Compress-Archive`` 非常慢（几万个小文件），
    而且旧版实现对超大 zip 支持不好；
  - Git Bash 里的 ``tar`` 可能是 GNU tar（``-a`` 只支持 gzip/bzip2/xz），
    用它加 ``.zip`` 后缀会生成"tar 改名成 zip"的假包。

所以统一走 Python 的 zipfile：
  - ``allowZip64=True``：单文件/总大小超过 4GB 也能正常写；
  - ``ZIP_DEFLATED``：二进制多的包里，文本与 .pyc 能压掉可观的一部分；
  - 软链接按 Unix symlink 条目保存（external_attr 带上 S_IFLNK），
    macOS 的 Archive Utility / Keka 解压后会还原成软链接，
    这一点对 PyQt6 的 .framework 布局是必需的。

用法:
    python scripts/make_release_zip.py <src_dir> <out_zip>
"""
from __future__ import annotations

import os
import stat
import sys
import zipfile


def _add_symlink(zf: zipfile.ZipFile, path: str, arcname: str) -> None:
    """按 Unix symlink 条目写入（内容是链接目标路径）。"""
    target = os.readlink(path)
    info = zipfile.ZipInfo(arcname)
    info.create_system = 3  # Unix
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    info.compress_type = zipfile.ZIP_STORED
    zf.writestr(info, target)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: make_release_zip.py <src_dir> <out_zip>", file=sys.stderr)
        return 2

    src = os.path.abspath(argv[1])
    out = os.path.abspath(argv[2])
    if not os.path.isdir(src):
        print(f"[ERR] not a directory: {src}", file=sys.stderr)
        return 1

    base = os.path.dirname(src)
    count = 0
    total = 0
    with zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
    ) as zf:
        for root, dirs, files in os.walk(src, followlinks=False):
            dirs.sort()
            for name in sorted(dirs + files):
                p = os.path.join(root, name)
                arc = os.path.relpath(p, base)
                if os.path.islink(p):
                    _add_symlink(zf, p, arc)
                    count += 1
                    continue
                if os.path.isdir(p):
                    continue
                zf.write(p, arc)
                count += 1
                total += os.path.getsize(p)
                if count % 5000 == 0:
                    print(f"[zip] {count} files, {total / 1e9:.2f} GB raw", flush=True)

    size = os.path.getsize(out)
    print(f"[zip] wrote {out}: {count} entries, raw {total / 1e9:.2f} GB -> zip {size / 1e9:.2f} GB")
    if size >= 2147483648:
        print(
            f"[ERR] zip exceeds GitHub Release asset limit (2147483648 bytes): {size}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
