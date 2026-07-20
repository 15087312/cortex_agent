#!/usr/bin/env python3
"""
视觉理解测试脚本 — 模型保持内存

用法:
    python scripts/test_vision.py              # 单次测试
    python scripts/test_vision.py --loop       # 循环测试
"""
import sys
import os
import time
import asyncio
import argparse
import base64
import tempfile
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_vision():
    from utils.screen_capture import capture_screen
    from mlx_vlm import load, generate
    from mlx_vlm.utils import load_config
    from mlx_vlm.prompt_utils import apply_chat_template
    from PIL import Image

    # 截图
    screenshot = capture_screen()
    if not screenshot:
        print("截图失败")
        return

    # 处理图片
    img = Image.open(io.BytesIO(base64.b64decode(screenshot)))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    if max(img.size) > 768:
        ratio = 768 / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        f.write(buf.getvalue())
        tmp = f.name

    # 推理
    prompt = apply_chat_template(
        processor, config,
        [{"role": "user", "content": [
            {"type": "image", "image": tmp},
            {"type": "text", "text": "描述这个屏幕的内容"}
        ]}],
        num_images=1
    )
    t0 = time.time()
    output = generate(model, processor, prompt=prompt, image=tmp, max_tokens=128)
    elapsed = time.time() - t0

    text = output.text if hasattr(output, "text") else str(output)
    print(f"\n[{elapsed:.2f}s] {text[:200]}...")

    os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description="视觉理解测试")
    parser.add_argument("--loop", action="store_true", help="循环测试")
    parser.add_argument("--interval", type=float, default=10, help="循环间隔")
    args = parser.parse_args()

    global model, processor, config

    print("加载 MLX-VLM 模型...")
    t0 = time.time()
    from mlx_vlm import load
    from mlx_vlm.utils import load_config
    model, processor = load("mlx-community/Qwen2.5-VL-3B-Instruct-4bit")
    config = load_config("mlx-community/Qwen2.5-VL-3B-Instruct-4bit")
    print(f"模型加载: {time.time()-t0:.1f}s\n")

    if args.loop:
        try:
            while True:
                asyncio.run(run_vision())
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已退出")
    else:
        asyncio.run(run_vision())


if __name__ == "__main__":
    main()
