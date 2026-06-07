"""
记忆过期检查工具
"""


class ExpiryChecker:
    """记忆过期检查器"""
    
    @staticmethod
    def is_expired(created_at: str, ttl: int) -> bool:
        """检查是否过期"""
        from datetime import datetime
        
        created = datetime.fromisoformat(created_at)
        now = datetime.now()
        
        return (now - created).total_seconds() > ttl
