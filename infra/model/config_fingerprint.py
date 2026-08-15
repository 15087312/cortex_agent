"""模型配置指纹 — 让缓存中的模型 client 感知配置变更（实时生效，无需重启）

背景：
  chat_light 的 ModelRunner.client / ContextSlicer._client 会缓存 LargeModelClient
  实例。用户在设置页修改模型 API URL/Key/名称后，缓存实例仍持有旧配置 → 请求 404 /
  仍是旧模型。心理活动之所以能立即生效，是因为它每次 think 都新建 SmallModelClient。

  本模块提供"配置指纹"：URL/Key/名称/格式任一变化 → 指纹变化。
  缓存持有者比对指纹，变化即重建 client。
"""


def model_config_fingerprint(tier: str) -> tuple:
    """返回指定层级（large/medium/small）模型配置的指纹元组。

    任一配置项（API_URL / API_KEY / NAME / API_FORMAT）变化 → 指纹变化。
    """
    from config.settings import settings
    prefix = f"{tier.upper()}_MODEL_"
    return (
        getattr(settings, prefix + "API_URL", ""),
        getattr(settings, prefix + "API_KEY", ""),
        getattr(settings, prefix + "NAME", ""),
        getattr(settings, prefix + "API_FORMAT", ""),
    )


def close_client_session(client) -> None:
    """尽力关闭旧 model client 的 aiohttp session（配置变更重建时调用，非阻塞）"""
    if client is None:
        return
    try:
        import asyncio
        sess = getattr(client, "_session", None)
        if sess is not None and not sess.closed:
            try:
                asyncio.ensure_future(sess.close())
            except RuntimeError:
                sess.close()  # 无运行中 loop：同步发起关闭（aiohttp 兼容）
    except Exception:
        pass
