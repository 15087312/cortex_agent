"""
黑匣子记忆

记录所有思考步骤、模块调用链路、情绪变化、自修改行为。
用于监控、审计、回溯、故障自愈、复盘进化。
"""
import os
import json
import time
import threading
from typing import Dict, Any, List, Optional
from pathlib import Path
from utils.logger import setup_logger


from modules.memory.utils.common import safe_timestamp as _safe_ts


class BlackboxMemory:
    """
    黑匣子记忆管理器
    
    负责：
    - 记录所有思考步骤
    - 记录模块调用链路
    - 记录情绪变化
    - 记录自修改行为
    - 用于自省、复盘、进化
    """

    def __init__(self, data_dir: str = "data/memory/blackbox"):
        """
        初始化黑匣子记忆
        
        Args:
            data_dir: 存储目录
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("blackbox_memory")
        
        # 不同类型的黑匣子日志
        self.log_files = {
            "thinking": self.data_dir / "thinking_log.jsonl",
            "module_call": self.data_dir / "module_call_log.jsonl",
            "emotion": self.data_dir / "emotion_log.jsonl",
            "evolution": self.data_dir / "evolution_log.jsonl",
            "error": self.data_dir / "error_log.jsonl"
        }
        
        # 确保文件存在
        for file_path in self.log_files.values():
            if not file_path.exists():
                file_path.touch()
        
        # 内存缓存（最近 100 条）
        self.recent_logs: Dict[str, List[Dict[str, Any]]] = {
            log_type: [] for log_type in self.log_files.keys()
        }
        self.max_cache_size = 100
        self._write_lock = threading.Lock()
        
        self.logger.info("黑匣子记忆初始化完成 (目录: %s)", self.data_dir)

    def log_thinking(self, thought_chain: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录思考过程
        
        Args:
            thought_chain: 思考链数据
            
        Returns:
            记录的日志条目
        """
        log_entry = {
            "id": f"bx_think_{int(time.time())}_{hash(str(thought_chain)) % 10000}",
            "type": "thinking",
            "timestamp": time.time(),
            "data": thought_chain
        }
        
        self._append_log("thinking", log_entry)
        self.logger.debug("记录思考过程: %s", log_entry["id"])
        
        return log_entry

    def log_module_call(
        self,
        caller: str,
        callee: str,
        action: str,
        details: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        记录模块调用
        
        Args:
            caller: 调用方
            callee: 被调用方
            action: 动作
            details: 详细信息
            
        Returns:
            记录的日志条目
        """
        log_entry = {
            "id": f"bx_call_{int(time.time())}_{hash(str(caller) + str(callee)) % 10000}",
            "type": "module_call",
            "timestamp": time.time(),
            "data": {
                "caller": caller,
                "callee": callee,
                "action": action,
                "details": details or {}
            }
        }
        
        self._append_log("module_call", log_entry)
        self.logger.debug("记录模块调用: %s -> %s (%s)", caller, callee, action)
        
        return log_entry

    def log_emotion(self, emotion_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录情绪变化
        
        Args:
            emotion_data: 情绪数据
            
        Returns:
            记录的日志条目
        """
        log_entry = {
            "id": f"bx_emotion_{int(time.time())}_{hash(str(emotion_data)) % 10000}",
            "type": "emotion",
            "timestamp": time.time(),
            "data": emotion_data
        }
        
        self._append_log("emotion", log_entry)
        self.logger.debug("记录情绪变化: %s", emotion_data.get("emotion"))
        
        return log_entry

    def log_evolution(self, evolution_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录自进化行为
        
        Args:
            evolution_data: 进化数据
            
        Returns:
            记录的日志条目
        """
        log_entry = {
            "id": f"bx_evolution_{int(time.time())}_{hash(str(evolution_data)) % 10000}",
            "type": "evolution",
            "timestamp": time.time(),
            "data": evolution_data
        }
        
        self._append_log("evolution", log_entry)
        self.logger.info("记录自进化行为: %s", log_entry["id"])
        
        return log_entry

    def log_error(self, error_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录错误
        
        Args:
            error_data: 错误数据
            
        Returns:
            记录的日志条目
        """
        log_entry = {
            "id": f"bx_error_{int(time.time())}_{hash(str(error_data)) % 10000}",
            "type": "error",
            "timestamp": time.time(),
            "data": error_data
        }
        
        self._append_log("error", log_entry)
        self.logger.error("记录错误: %s", error_data.get("error", "未知错误"))
        
        return log_entry

    def _append_log(self, log_type: str, log_entry: Dict[str, Any]) -> None:
        """
        追加日志到文件

        Args:
            log_type: 日志类型
            log_entry: 日志条目
        """
        if log_type not in self.log_files:
            raise ValueError(f"不支持的日志类型: {log_type}")

        file_path = self.log_files[log_type]

        try:
            # 写入文件（线程安全）
            with self._write_lock:
                with open(file_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

            # 更新内存缓存
            self.recent_logs[log_type].append(log_entry)
            if len(self.recent_logs[log_type]) > self.max_cache_size:
                self.recent_logs[log_type] = self.recent_logs[log_type][-self.max_cache_size:]
        except Exception as e:
            self.logger.error("写入黑匣子日志失败: %s", e)

    def get_logs(
        self,
        log_type: str,
        limit: int = 50,
        reverse: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取日志
        
        Args:
            log_type: 日志类型
            limit: 返回数量限制
            reverse: 是否倒序
            
        Returns:
            日志列表
        """
        if log_type not in self.log_files:
            raise ValueError(f"不支持的日志类型: {log_type}")
        
        # 优先从内存缓存读取
        if log_type in self.recent_logs and len(self.recent_logs[log_type]) >= limit:
            logs = self.recent_logs[log_type]
            if reverse:
                return list(reversed(logs[-limit:]))
            return logs[-limit:]
        
        # 从文件读取
        file_path = self.log_files[log_type]
        logs = []
        
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                logs.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                
                # 按时间排序
                logs.sort(key=_safe_ts, reverse=reverse)
                
                return logs[:limit]
            except Exception as e:
                self.logger.error("读取黑匣子日志失败: %s", e)
        
        return []

    def search_logs(self, log_type: str, keywords: List[str], limit: int = 20) -> List[Dict[str, Any]]:
        """
        搜索日志
        
        Args:
            log_type: 日志类型
            keywords: 关键词列表
            limit: 返回数量限制
            
        Returns:
            匹配的日志列表
        """
        logs = self.get_logs(log_type, limit=500)
        
        results = []
        for log in logs:
            content_str = json.dumps(log.get("data", {}), ensure_ascii=False).lower()
            score = sum(1 for kw in keywords if kw.lower() in content_str)
            
            if score > 0:
                results.append({**log, "search_score": score})
        
        # 按相关度排序
        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)
        
        return results[:limit]

    def get_timeline(self, start_time: float = None, end_time: float = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取时间线（所有类型的日志混合）
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            limit: 返回数量限制
            
        Returns:
            时间线日志列表
        """
        all_logs = []
        
        for log_type in self.log_files.keys():
            logs = self.get_logs(log_type, limit=limit)
            all_logs.extend(logs)
        
        # 按时间排序
        all_logs.sort(key=_safe_ts, reverse=True)
        
        # 时间过滤
        if start_time:
            all_logs = [log for log in all_logs if log.get("timestamp", 0) >= start_time]
        if end_time:
            all_logs = [log for log in all_logs if log.get("timestamp", 0) <= end_time]
        
        return all_logs[:limit]

    def get_statistics(self) -> Dict[str, Any]:
        """获取黑匣子记忆统计"""
        stats = {}
        total_size = 0
        
        for log_type, file_path in self.log_files.items():
            if file_path.exists():
                file_size = file_path.stat().st_size
                total_size += file_size
                
                # 计算记录数
                count = 0
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            count += 1
                
                stats[log_type] = {
                    "count": count,
                    "size_kb": file_size / 1024
                }
        
        stats["total_size_kb"] = total_size / 1024
        stats["cache_size"] = {k: len(v) for k, v in self.recent_logs.items()}
        
        return stats

    def clear_logs(self, log_type: str = None) -> int:
        """
        清空日志
        
        Args:
            log_type: 日志类型（可选，不指定则清空所有）
            
        Returns:
            清空的记录数
        """
        cleared_count = 0
        types_to_clear = [log_type] if log_type else list(self.log_files.keys())
        
        for lt in types_to_clear:
            if lt in self.log_files:
                file_path = self.log_files[lt]
                
                if file_path.exists():
                    # 统计记录数
                    count = 0
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                count += 1
                    
                    # 清空文件
                    file_path.write_text('')
                    cleared_count += count
                    
                    # 清空缓存
                    self.recent_logs[lt] = []
                    
                    self.logger.info("清空黑匣子日志 [%s]: %d 条", lt, count)
        
        return cleared_count
