#!/usr/bin/env python3
"""
OCR 测试脚本

用法:
    python scripts/test_ocr.py           # 直接截图
    python scripts/test_ocr.py --delay 3 # 延迟3秒截图
    python scripts/test_ocr.py --loop    # 循环测试
"""
import sys
import os
import time
import argparse
import base64
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_ocr():
    from utils.screen_capture import capture_screen
    from rapidocr_onnxruntime import RapidOCR

    screenshot = capture_screen()
    if not screenshot:
        print("截图失败")
        return

    img_data = base64.b64decode(screenshot)
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(img_data)
        tmp_path = f.name

    ocr = RapidOCR()
    t0 = time.time()
    result, _ = ocr(tmp_path)
    elapsed = time.time() - t0

    os.unlink(tmp_path)

    # 过滤并显示
    texts = []
    for item in result:
        text = item[1]
        confidence = float(item[2])
        if confidence > 0.5:
            texts.append((confidence, text))

    print(f"\n{'─' * 50}")
    print(f"识别到 {len(texts)} 段文字 (耗时 {elapsed:.2f}s)")
    print(f"{'─' * 50}")
    for conf, text in texts:
        print(f"  [{conf:.2f}] {text}")
    print()


def main():
    parser = argparse.ArgumentParser(description="OCR 测试")
    parser.add_argument("--delay", type=float, default=0, help="截图前延迟秒数")
    parser.add_argument("--loop", action="store_true", help="循环测试")
    parser.add_argument("--interval", type=float, default=5, help="循环间隔")
    args = parser.parse_args()

    print("=" * 50)
    print("  OCR 测试 (Ctrl+C 退出)")
    print("=" * 50)

    if args.loop:
        try:
            while True:
                if args.delay > 0:
                    print(f"\n等待 {args.delay}s...")
                    time.sleep(args.delay)
                run_ocr()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已退出")
    else:
        if args.delay > 0:
            print(f"等待 {args.delay}s...")
            time.sleep(args.delay)
        run_ocr()


if __name__ == "__main__":
    main()
