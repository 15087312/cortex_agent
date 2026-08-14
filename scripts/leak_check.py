#!/usr/bin/env python3
"""内存泄漏精确定位工具（配合 conftest 默认开启的趋势检测）。

conftest 已默认开启两层泄漏检测（趋势采样 + pympler 类型定位）。
本脚本用于在检测到泄漏嫌疑后，缩小范围精确定位：

用法:
  python scripts/leak_check.py                          # 跑 tests/unit 全量
  python scripts/leak_check.py tests/unit/test_pet_engine.py
  python scripts/leak_check.py -k "not web_search"      # 透传 pytest 参数

输出:
  pytest 结束后，conftest 的 [LEAK-DETECT] 报告会打印增长趋势 + pympler 类型 top。
  对同一测试文件重复跑并对比：若某类型数量随测试数翻倍，即为泄漏候选。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    args = sys.argv[1:]
    targets = args or ["tests/unit"]
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-s"] + targets
    print(f"[leak_check] 运行: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ))
    print(f"\n[leak_check] pytest 退出码: {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
