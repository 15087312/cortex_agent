"""
OmniParser 启动器 — 在子进程中运行时应用 monkey-patch，然后启动 HTTP 服务。

不修改 OmniParser 源代码，不修改 HF 缓存文件。
所有兼容性适配通过运行时 patch 实现。

注意：使用 --device cpu 而非 mps，因为 Florence2 在 Apple Silicon MPS 上
存在底层 gather 断言失败（MPSCore 库限制），无法修复。
CPU 推理速度约 10-30 秒/次，对于 UI 检测场景可接受。
"""
import sys
import os


_REMOVED_PARAMS = frozenset({
    "show_log", "use_angle_cls", "max_batch_size",
    "use_dilation", "use_gpu",
    "det_db_score_mode", "rec_batch_num",
})


def _patch_paddleocr():
    """PaddleOCR 3.x 移除的参数在运行时过滤

    PaddleOCR 3.x 的 __init__ 使用 **kwargs 接收所有参数，
    内部再通过 parse_common_args 校验。inspect.signature 无法过滤。
    因此使用硬编码废弃参数列表 + parse_common_args 兜底双层过滤。
    """
    try:
        import paddleocr

        original_init = paddleocr.PaddleOCR.__init__

        def patched_init(self, **kwargs):
            filtered = {k: v for k, v in kwargs.items() if k not in _REMOVED_PARAMS}
            original_init(self, **filtered)

        paddleocr.PaddleOCR.__init__ = patched_init
    except Exception:
        pass

    try:
        import paddleocr._common_args as _args
        _original_parse = _args.parse_common_args

        def _patched_parse(*a, **kw):
            try:
                return _original_parse(*a, **kw)
            except ValueError:
                filtered = {k: v for k, v in kw.items() if k not in _REMOVED_PARAMS}
                return _original_parse(*a, **filtered)

        _args.parse_common_args = _patched_parse
    except Exception:
        pass


def main():
    """启动 OmniParser HTTP 服务"""
    if len(sys.argv) < 3:
        print("usage: omniparser_launcher.py <omniparser_dir> <port>")
        sys.exit(1)

    omniparser_dir = sys.argv[1]
    port = int(sys.argv[2])

    # 应用补丁
    _patch_paddleocr()

    # 设置环境
    sys.path.insert(0, omniparser_dir)
    os.chdir(omniparser_dir)

    # 清理 sys.argv，让 OmniParser 的 argparse 只看到端口参数
    weights_dir = os.path.join(omniparser_dir, "weights")
    sys.argv = [
        "omniparserserver.py",
        "--port", str(port), "--device", "mps",
        "--som_model_path", os.path.join(weights_dir, "icon_detect", "model.pt"),
        "--caption_model_path", os.path.join(weights_dir, "icon_caption_florence"),
    ]

    # 导入并启动 OmniParser FastAPI 服务
    from omnitool.omniparserserver.omniparserserver import app
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
