"""
记忆过期检查工具
"""


class ExpiryChecker:
    """记忆过期检查器"""

    @staticmethod
    def is_expired(created_at: str, ttl: int) -> bool:
        """检查是否过期"""
        from datetime import datetime, timezone

        created = datetime.fromisoformat(created_at)
        # 统一时区：如果 created 是 aware datetime，now 也用 aware；否则用 naive
        if created.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()

        return (now - created).total_seconds() > ttl
