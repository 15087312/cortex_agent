#!/usr/bin/env python3
"""泄漏检测能力验证：逐个跑泄漏测试，断言检测系统行为正确。

tests/leak/ 分两类：
- 故意泄漏（能力验证）：构造真实泄漏，conftest 检测系统（muppy 字节采样 +
  趋势判定）必须报告 ⚠ 疑似内存泄漏
- 内存安全回归：断言有界/可回收，检测器不应报警（报警 = 误报）

用法:
  python scripts/verify_leak_detection.py

退出码: 0=全部通过，1=有失败
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_MARK = "⚠ 疑似内存泄漏"

# 泄漏测试分两类：
# 1) 故意泄漏（能力验证）：构造真实泄漏，检测器必须报警
# 2) 内存安全回归：断言有界/可回收，检测器不应报警（报警=误报）
#    安全类用更高阈值（500 KiB/测试），忽略 import 预热漂移，仍能捕捉无界增长
SAFETY_ONLY = {
    "test_leak_blackboard_state.py",   # 委托链上限/观察有界
    "test_leak_client_rebuild.py",     # client 重建时 session 关闭
    "test_leak_conscience_dialogs.py", # 心理活动缓存有界
    "test_leak_todo_persistence.py",   # todo 持久化不累积
}
ENV_DELIBERATE = {"LEAK_INTERVAL": "1", "LEAK_RATE_THRESHOLD": "50"}
ENV_SAFETY = {"LEAK_INTERVAL": "1", "LEAK_RATE_THRESHOLD": "500"}
# 泄漏测试分两类：
# 1) 故意泄漏（能力验证）：构造真实泄漏，检测器必须报警
# 2) 内存安全回归：断言有界/可回收，检测器不应报警（报警=误报）
#    安全类用更高阈值（500 KiB/测试），忽略 import 预热漂移，仍能捕捉无界增长
SAFETY_ONLY = {
    "test_leak_blackboard_state.py",   # 委托链上限/观察有界
    "test_leak_client_rebuild.py",     # client 重建时 session 关闭
    "test_leak_conscience_dialogs.py", # 心理活动缓存有界
    "test_leak_todo_persistence.py",   # todo 持久化不累积
}
ENV_DELIBERATE = {"LEAK_INTERVAL": "1", "LEAK_RATE_THRESHOLD": "50"}
ENV_SAFETY = {"LEAK_INTERVAL": "1", "LEAK_RATE_THRESHOLD": "500"}


def run_single(test_file: str) -> str:
    env = dict(os.environ)
    env.update(ENV_SAFETY if os.path.basename(test_file) in SAFETY_ONLY else ENV_DELIBERATE)
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
        is_safety = name in SAFETY_ONLY
        try:
            out = run_single(f)
            detected = ALERT_MARK in out
            if is_safety:
                ok = not detected
                mark = "✅" if ok else "❌"
                print(f"{mark} {name}: {'内存安全（无误报）' if ok else '误报！安全测试被判定泄漏'}")
            else:
                ok = detected
                mark = "✅" if ok else "❌"
                print(f"{mark} {name}: {'检测到泄漏' if detected else '未检测到泄漏'}")
            results.append((name, ok))
            if not ok:
                # 打印趋势段辅助排查
                for line in out.splitlines():
                    if "趋势" in line or "采样点" in line:
                        print(f"      {line.strip()}")
        except subprocess.TimeoutExpired:
            results.append((name, False))
            print(f"❌ {name}: 运行超时")

    ok = sum(1 for _, d in results if d)
    print(f"\n结果: {ok}/{len(results)} 通过"
          f"（故意泄漏 {len(files) - len(SAFETY_ONLY)} 项须检出、安全回归 {len(SAFETY_ONLY)} 项须无误报）")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
