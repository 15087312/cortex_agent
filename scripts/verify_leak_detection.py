#!/usr/bin/env python3
"""泄漏检测能力验证：逐个跑泄漏测试，断言每种类型都被检测系统识别。

每种泄漏测试（tests/leak/）故意构造特定类型/模块的泄漏；
conftest 的检测系统（muppy 字节采样 + 趋势判定）应报告 ⚠ 疑似内存泄漏。

用法:
  python scripts/verify_leak_detection.py

退出码: 0=全部检测到，1=有未检测到的类型
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_MARK = "⚠ 疑似内存泄漏"
# 泄漏测试量级不一，用较小的采样间隔 + 较低阈值确保各类都能体现趋势
ENV_EXTRA = {"LEAK_INTERVAL": "15", "LEAK_RATE_THRESHOLD": "50"}


def run_single(test_file: str) -> str:
    env = dict(os.environ)
    env.update(ENV_EXTRA)
    cmd = [
        sys.executable, "-m", "pytest", test_file,
        "-q", "-p", "no:cacheprovider", "-m", "leak", "-s",
    ]
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    return r.stdout + r.stderr


def main() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "leak", "test_leak_*.py")))
    if not files:
        print("未找到泄漏测试（tests/leak/test_leak_*.py）")
        return 1

    results = []
    for f in files:
        name = os.path.basename(f)
        try:
            out = run_single(f)
            detected = ALERT_MARK in out
            results.append((name, detected))
            mark = "✅" if detected else "❌"
            print(f"{mark} {name}: {'检测到泄漏' if detected else '未检测到泄漏'}")
            if not detected:
                # 打印趋势段辅助排查
                for line in out.splitlines():
                    if "趋势" in line or "采样点" in line:
                        print(f"      {line.strip()}")
        except subprocess.TimeoutExpired:
            results.append((name, False))
            print(f"❌ {name}: 运行超时")

    ok = sum(1 for _, d in results if d)
    print(f"\n结果: {ok}/{len(results)} 种泄漏被检测系统识别")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
