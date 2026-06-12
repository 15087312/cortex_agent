"""Stub — 模块已删除，保留此文件兼容旧导入"""
class GlobalContextPool:
    def __getattr__(self, name):
        return lambda *a, **kw: None
gcm_pool = GlobalContextPool()
