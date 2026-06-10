"""一键下载可选模型组件

用法:
  python scripts/setup_models.py              # 交互式选择
  python scripts/setup_models.py --all        # 下载全部
  python scripts/setup_models.py omniparser   # 只下载指定项
"""

import argparse
import os
import subprocess
import sys

# ── 可下载组件定义 ──

COMPONENTS = {
    "omniparser": {
        "name": "OmniParser UI 检测",
        "description": "微软 UI 截图解析（YOLO 检测 + Florence2 描述），用于 learn_tool 和高精度屏幕理解",
        "repo": "microsoft/OmniParser-v2.0",
        "files": [
            "icon_detect/train_args.yaml",
            "icon_detect/model.pt",
            "icon_detect/model.yaml",
            "icon_caption/config.json",
            "icon_caption/generation_config.json",
            "icon_caption/model.safetensors",
        ],
        "target_dir": "OmniParser/weights",
        "post_cmd": "mv OmniParser/weights/icon_caption OmniParser/weights/icon_caption_florence",
        "size": "~1.5 GB",
    },
    "qwen-vl-2b": {
        "name": "Qwen2-VL-2B (transformers)",
        "description": "通义千问视觉模型 2B 版，用于 understand_screen 本地识图（需 NVIDIA GPU）",
        "repo": "Qwen/Qwen2-VL-2B-Instruct",
        "files": [],  # 整个仓库
        "target_dir": "models/qwen2-vl-2b",
        "post_cmd": None,
        "size": "~4 GB",
    },
    "qwen-vl-7b-mlx": {
        "name": "Qwen2-VL-7B-4bit (MLX)",
        "description": "通义千问视觉模型 7B 4-bit 量化版，Apple Silicon 专用（M1/M2/M3/M4）",
        "repo": "mlx-community/Qwen2-VL-7B-Instruct-4bit",
        "files": [],
        "target_dir": "models/qwen2-vl-7b-mlx",
        "post_cmd": None,
        "size": "~4.5 GB",
    },
}


def check_huggingface_cli():
    """检查 huggingface-cli 是否可用"""
    try:
        subprocess.run(
            ["huggingface-cli", "--version"],
            capture_output=True, check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def install_huggingface_cli():
    """安装 huggingface-hub CLI"""
    print("正在安装 huggingface-hub...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "huggingface_hub[cli]"],
        check=True,
    )


def download_component(key: str, project_root: str):
    """下载单个组件"""
    comp = COMPONENTS[key]
    target = os.path.join(project_root, comp["target_dir"])
    os.makedirs(target, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"下载: {comp['name']} ({comp['size']})")
    print(f"仓库: {comp['repo']}")
    print(f"目标: {target}")
    print(f"{'='*50}")

    cmd = ["huggingface-cli", "download", comp["repo"]]

    if comp["files"]:
        for f in comp["files"]:
            cmd.append(f)
        cmd.extend(["--local-dir", target])
    else:
        cmd.extend(["--local-dir", target])

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"  ✗ 下载失败: {e}")
        return False

    # 后处理（如重命名目录）
    if comp["post_cmd"]:
        print(f"  后处理: {comp['post_cmd']}")
        post_cmd = comp["post_cmd"]
        # 相对路径转绝对路径
        if not os.path.isabs(post_cmd.split()[1]):
            post_cmd = post_cmd.replace(
                comp["target_dir"],
                os.path.join(project_root, comp["target_dir"]),
                1,
            )
        # 只在目标存在时执行
        parts = post_cmd.split()
        if os.path.exists(parts[1]) or "mv" not in parts[0]:
            subprocess.run(post_cmd, shell=True, cwd=project_root)

    print(f"  ✓ {comp['name']} 下载完成")
    return True


def interactive_select():
    """交互式选择要下载的组件"""
    print("\n可下载组件:\n")
    keys = list(COMPONENTS.keys())
    for i, key in enumerate(keys, 1):
        comp = COMPONENTS[key]
        # 检查是否已下载
        target = comp["target_dir"]
        exists = os.path.exists(target) and os.listdir(target)
        status = " ✓ 已安装" if exists else ""
        print(f"  [{i}] {comp['name']}{status}")
        print(f"      {comp['description']}")
        print(f"      大小: {comp['size']}")
        print()

    print(f"  [0] 全部下载\n")

    try:
        choice = input("选择编号（多个用空格分隔，如 1 3）: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消")
        return []

    if not choice:
        return []

    if choice == "0":
        return keys

    selected = []
    for c in choice.split():
        try:
            idx = int(c) - 1
            if 0 <= idx < len(keys):
                selected.append(keys[idx])
        except ValueError:
            pass
    return selected


def main():
    parser = argparse.ArgumentParser(description="下载可选模型组件")
    parser.add_argument(
        "components",
        nargs="*",
        choices=list(COMPONENTS.keys()),
        help="指定要下载的组件（不指定则交互式选择）",
    )
    parser.add_argument("--all", action="store_true", help="下载全部组件")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 确保 huggingface-cli 可用
    if not check_huggingface_cli():
        try:
            install_huggingface_cli()
        except Exception as e:
            print(f"安装 huggingface-hub 失败: {e}")
            print(f"请手动运行: pip install 'huggingface_hub[cli]'")
            sys.exit(1)

    # 确定要下载的组件
    if args.all:
        selected = list(COMPONENTS.keys())
    elif args.components:
        selected = args.components
    else:
        selected = interactive_select()

    if not selected:
        print("未选择任何组件")
        return

    print(f"\n将下载 {len(selected)} 个组件...")
    success = 0
    for key in selected:
        if download_component(key, project_root):
            success += 1

    print(f"\n完成: {success}/{len(selected)} 个组件下载成功")


if __name__ == "__main__":
    main()
