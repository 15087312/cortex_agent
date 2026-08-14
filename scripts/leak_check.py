#!/usr/bin/env python3
"""内存泄漏定位工具：RSS 实时监控 + tracemalloc 精确定位。

conftest 已默认开启泄漏检测（pympler.muppy 字节采样 + 趋势判定 + 类型 diff）。
本脚本提供更精确的定位能力：
  1. RSS 监控：外部采样 pytest 子进程真实内存（含 C 扩展/numpy 分配的堆外内存），
     检测是否随测试数线性增长 —— 无侵入、不拖慢测试。
  2. tracemalloc 定位：对小测试集启用 tracemalloc，输出存活内存分配位置 top。

用法:
  python scripts/leak_check.py                          # RSS 监控跑 tests/unit 全量
  python scripts/leak_check.py tests/unit/test_xxx.py   # 缩小范围定位
  python scripts/leak_check.py -k "not web_search"
  python scripts/leak_check.py --tracemalloc tests/unit/test_xxx.py   # 精确定位分配点

输出:
  - RSS 采样点 + 线性增长率判定（每测试新增 MB）
  - pytest 结束后 conftest 的 [LEAK-DETECT] 报告（muppy 字节趋势 + pympler 类型）
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RSS_SAMPLE_INTERVAL = 5.0  # 秒


def _rss_mb(pid: int) -> float:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out) / 1024.0
    except Exception:
        return 0.0


def _monitor_rss(cmd: list) -> None:
    """启动 pytest 子进程并采样 RSS，检测线性增长。"""
    proc = subprocess.Popen(cmd, cwd=ROOT)
    samples = []
    start = time.time()
    while proc.poll() is None:
        samples.append((time.time() - start, _rss_mb(proc.pid)))
        time.sleep(RSS_SAMPLE_INTERVAL)
    rc = proc.wait()

    print(f"\n[leak_check] pytest 退出码: {rc}  耗时 {time.time()-start:.0f}s")
    if len(samples) < 3:
        print("[leak_check] RSS 采样不足，跳过增长判定")
        return
    print("[leak_check] RSS 采样（时间s, MB）:")
    for t, mb in samples:
        print(f"    t={t:5.0f}s  RSS={mb:7.1f}MB")
    # 后半段线性判定
    half = samples[len(samples) // 2:]
    d_mb = half[-1][1] - half[0][1]
    d_t = max(1.0, half[-1][0] - half[0][0])
    rate_mb_per_s = d_mb / d_t
    print(f"[leak_check] 后半段 RSS 变化: {half[0][1]:.1f} → {half[-1][1]:.1f} MB "
          f"({rate_mb_per_s:+.2f} MB/s)")
    if rate_mb_per_s > 0.5:
        print("[leak_check] ⚠ RSS 持续增长，疑似内存泄漏")
    else:
        print("[leak_check] ✓ RSS 稳定/收敛")


def main() -> int:
    args = sys.argv[1:]
    use_tracemalloc = False
    if args and args[0] == "--tracemalloc":
        use_tracemalloc = True
        args = args[1:]

    targets = args or ["tests/unit"]
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-s"] + targets
    if use_tracemalloc:
        # tracemalloc 全程（慢，适合小范围精确定位）
        cmd = ["python", "-X", "tracemalloc", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-s"] + targets
        print(f"[leak_check] tracemalloc 模式（较慢）: {' '.join(cmd)}", flush=True)
        proc = subprocess.run(cmd, cwd=ROOT)
        print(f"[leak_check] pytest 退出码: {proc.returncode}")
        return proc.returncode

    print(f"[leak_check] RSS 监控运行: {' '.join(cmd)}", flush=True)
    _monitor_rss(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
