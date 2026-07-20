"""
记忆匹配工具 - 多维度记忆检索与评分

提供四维度记忆匹配评分：
- 语义相似度 (40%)
- 关键词匹配 (20%)
- 时间衰减 (20%)
- 重要性权重 (20%)
"""
import json
import math
from datetime import datetime
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger
from infra.tool_manager.tool_registry import ToolRegistry

logger = setup_logger("memory_matcher")


class MemoryMatchEngine:
    """多维度记忆匹配引擎"""
    
    # 权重配置
    SEMANTIC_WEIGHT = 0.4    # 语义相似度权重
    KEYWORD_WEIGHT = 0.2     # 关键词匹配权重
    TIME_DECAY_WEIGHT = 0.2  # 时间衰减权重
    IMPORTANCE_WEIGHT = 0.2  # 重要性权重
    
    def __init__(self):
        """初始化匹配引擎"""
        self._embedding_model = None
        self._model_loaded = False
        self._model_load_attempted = False
        self.logger = setup_logger("memory_match_engine")
    
    def _load_embedding_model(self) -> bool:
        """延迟加载 embedding 模型"""
        if self._model_load_attempted:
            return self._model_loaded
        
        self._model_load_attempted = True
        
        try:
            from sentence_transformers import SentenceTransformer
            
            # 从配置读取模型名或使用默认
            try:
                from config.settings import settings
                model_name = getattr(settings, 'EMBEDDING_MODEL', 'all-MiniLM-L6-v2')
                cache_folder = getattr(settings, 'EMBEDDING_CACHE_FOLDER', None)
                local_files_only = getattr(settings, 'EMBEDDING_LOCAL_FILES_ONLY', True)
            except Exception as e:
                self.logger.warning(f"加载 embedding 配置失败，使用默认: {e}")
                model_name = 'all-MiniLM-L6-v2'
                cache_folder = None
                local_files_only = True

            model_kwargs = {
                "cache_folder": cache_folder,
                "local_files_only": local_files_only,
            }
            try:
                self._embedding_model = SentenceTransformer(model_name, **model_kwargs)
            except TypeError:
                if local_files_only:
                    raise
                model_kwargs.pop("local_files_only", None)
                self._embedding_model = SentenceTransformer(model_name, **model_kwargs)
            self._model_loaded = True
            self.logger.info(f"Embedding 模型加载成功: {model_name}")
            return True
            
        except Exception as e:
            self.logger.warning(f"Embedding 模型加载失败: {e}，语义相似度将返回 0")
            self._model_loaded = False
            return False
    
    def get_embedding(self, text: str) -> Optional[Any]:
        """
        获取文本的 embedding 向量
        
        Args:
            text: 输入文本
            
        Returns:
            numpy 数组或 None（加载失败时）
        """
        if not self._load_embedding_model():
            return None
        
        try:
            import numpy as np
            embedding = self._embedding_model.encode(text, convert_to_numpy=True)
            return embedding
        except Exception as e:
            self.logger.error(f"生成 embedding 失败: {e}")
            return None
    
    def _semantic_similarity(self, query_embedding: Any, memory_embedding: Any) -> float:
        """
        计算语义相似度（余弦相似度）
        
        Args:
            query_embedding: 查询文本的 embedding
            memory_embedding: 记忆内容的 embedding
            
        Returns:
            0-1 之间的相似度分数
        """
        if query_embedding is None or memory_embedding is None:
            return 0.0
        
        try:
            import numpy as np
            
            # 确保是 numpy 数组
            if not isinstance(query_embedding, np.ndarray):
                query_embedding = np.array(query_embedding)
            if not isinstance(memory_embedding, np.ndarray):
                memory_embedding = np.array(memory_embedding)
            
            # 计算余弦相似度
            dot_product = np.dot(query_embedding, memory_embedding)
            norm_query = np.linalg.norm(query_embedding)
            norm_memory = np.linalg.norm(memory_embedding)
            
            if norm_query == 0 or norm_memory == 0:
                return 0.0
            
            cosine_sim = dot_product / (norm_query * norm_memory)
            
            # 归一化到 [0, 1]（余弦相似度范围是 [-1, 1]）
            return (cosine_sim + 1) / 2
            
        except Exception as e:
            self.logger.error(f"计算语义相似度失败: {e}")
            return 0.0
    
    def _keyword_overlap(self, query: str, memory_content: str) -> float:
        """
        计算关键词重叠率（Jaccard 系数）
        
        Args:
            query: 查询文本
            memory_content: 记忆内容
            
        Returns:
            0-1 之间的重叠率
        """
        try:
            import jieba
            
            # 使用 jieba 分词
            query_words = set(jieba.cut(query.lower()))
            memory_words = set(jieba.cut(memory_content.lower()))
            
            # 过滤空字符串和标点
            query_words = {w for w in query_words if w.strip() and len(w) > 1}
            memory_words = {w for w in memory_words if w.strip() and len(w) > 1}
            
        except Exception as e:
            self.logger.warning(f"jieba 分词失败，降级为简单字符匹配: {e}")
            query_words = set(query.lower())
            memory_words = set(memory_content.lower())
            # 过滤空白字符
            query_words = {w for w in query_words if w.strip()}
            memory_words = {w for w in memory_words if w.strip()}
        
        if not query_words or not memory_words:
            return 0.0
        
        # 计算 Jaccard 系数：|交集| / |并集|
        intersection = query_words & memory_words
        union = query_words | memory_words
        
        if not union:
            return 0.0
        
        return len(intersection) / len(union)
    
    def _time_decay(self, memory_timestamp: str, half_life_hours: float = 24.0) -> float:
        """
        计算时间衰减分数
        
        使用指数衰减公式：score = exp(-λ * Δt)
        其中 λ = ln(2) / half_life
        
        Args:
            memory_timestamp: 记忆时间戳（ISO 格式字符串）
            half_life_hours: 半衰期（小时），默认 24 小时
            
        Returns:
            0-1 之间的衰减分数
        """
        if not memory_timestamp:
            return 0.5  # 无时间戳时返回中等分数
        
        try:
            # 解析时间戳
            try:
                memory_time = datetime.fromisoformat(memory_timestamp.replace('Z', '+00:00'))
            except ValueError:
                # 尝试其他格式
                memory_time = datetime.strptime(memory_timestamp, '%Y-%m-%d %H:%M:%S')
            
            # 获取当前时间
            now = datetime.now(memory_time.tzinfo) if memory_time.tzinfo else datetime.now()
            
            # 计算时间差（小时）
            delta_hours = (now - memory_time).total_seconds() / 3600
            
            if delta_hours < 0:
                delta_hours = 0  # 未来时间按当前处理
            
            # 计算衰减系数 λ
            lambda_val = math.log(2) / half_life_hours
            
            # 指数衰减
            score = math.exp(-lambda_val * delta_hours)
            
            return max(0.0, min(1.0, score))
            
        except Exception as e:
            self.logger.debug(f"时间衰减计算失败: {e}")
            return 0.5  # 失败时返回中等分数
    
    def _importance_score(self, importance: float) -> float:
        """
        获取重要性分数
        
        Args:
            importance: 重要性值（0-1）
            
        Returns:
            0-1 之间的分数
        """
        if importance is None:
            return 0.5  # 缺失时默认 0.5
        
        try:
            importance_val = float(importance)
            return max(0.0, min(1.0, importance_val))
        except (TypeError, ValueError):
            return 0.5
    
    def score_single(
        self, 
        query: str, 
        memory: Dict[str, Any], 
        query_embedding: Any = None
    ) -> Dict[str, Any]:
        """
        对单条记忆进行综合评分
        
        Args:
            query: 查询文本
            memory: 记忆字典，格式：{"content": str, "timestamp": str, "importance": float, ...}
            query_embedding: 预计算的查询 embedding（可选）
            
        Returns:
            评分结果字典
        """
        content = memory.get("content", "")
        timestamp = memory.get("timestamp", "")
        importance = memory.get("importance", 0.5)
        
        # 语义相似度
        if query_embedding is not None:
            memory_embedding = memory.get("embedding")
            if memory_embedding is None and content:
                memory_embedding = self.get_embedding(content)
            semantic_score = self._semantic_similarity(query_embedding, memory_embedding)
        else:
            # 如果没有提供 query_embedding，尝试计算
            if content:
                q_emb = self.get_embedding(query)
                m_emb = self.get_embedding(content)
                semantic_score = self._semantic_similarity(q_emb, m_emb)
            else:
                semantic_score = 0.0
        
        # 关键词重叠
        keyword_score = self._keyword_overlap(query, content) if content else 0.0
        
        # 时间衰减
        time_decay_score = self._time_decay(timestamp)
        
        # 重要性
        importance_score = self._importance_score(importance)
        
        # 综合评分
        total_score = (
            self.SEMANTIC_WEIGHT * semantic_score +
            self.KEYWORD_WEIGHT * keyword_score +
            self.TIME_DECAY_WEIGHT * time_decay_score +
            self.IMPORTANCE_WEIGHT * importance_score
        )
        
        return {
            "total_score": round(total_score, 4),
            "semantic": round(semantic_score, 4),
            "keyword": round(keyword_score, 4),
            "time_decay": round(time_decay_score, 4),
            "importance": round(importance_score, 4),
            "memory": memory
        }
    
    def score_batch(
        self, 
        query: str, 
        memories: List[Dict[str, Any]], 
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        批量评分记忆列表
        
        Args:
            query: 查询文本
            memories: 记忆字典列表
            top_k: 可选，限制返回数量
            
        Returns:
            按 total_score 降序排序的评分结果列表
        """
        # 预计算 query 的 embedding
        query_embedding = self.get_embedding(query)
        
        # 批量评分
        results = []
        for memory in memories:
            try:
                score_result = self.score_single(query, memory, query_embedding)
                results.append(score_result)
            except Exception as e:
                self.logger.error(f"评分单条记忆失败: {e}")
                continue
        
        # 按 total_score 降序排序
        results.sort(key=lambda x: x["total_score"], reverse=True)
        
        # 限制返回数量
        if top_k is not None and top_k > 0:
            results = results[:top_k]
        
        return results


# 模块级引擎单例，避免重复加载 embedding 模型
_engine = MemoryMatchEngine()


@ToolRegistry.register(
    name="memory_match",
    description="对记忆列表进行多维度匹配评分（语义+关键词+时间+重要性），返回最相关的记忆",
    params={"query": "查询文本", "memory_items": "JSON格式的记忆列表", "top_k": "返回数量，默认5"},
    source="builtin",
    core=True,
)
def memory_match(query: str, memory_items: str, top_k: str = "5") -> str:
    """
    对记忆列表进行综合匹配评分
    
    Args:
        query: 查询文本
        memory_items: JSON 格式的记忆列表字符串，或直接传入列表
        top_k: 返回数量，默认 5
        
    Returns:
        JSON 格式的评分结果
    """
    try:
        # 解析 memory_items - 支持 JSON 字符串或列表
        if isinstance(memory_items, list):
            memories = memory_items
        else:
            try:
                memories = json.loads(memory_items)
            except (json.JSONDecodeError, TypeError) as e:
                return json.dumps({"error": f"JSON 解析失败: {str(e)}"}, ensure_ascii=False)
        
        if not isinstance(memories, list):
            return json.dumps({"error": "memory_items 必须是 JSON 数组"}, ensure_ascii=False)
        
        # 解析 top_k
        try:
            top_k_val = int(top_k)
        except ValueError:
            top_k_val = 5
        
        if not memories:
            return json.dumps({"results": [], "count": 0}, ensure_ascii=False)
        
        # 调用引擎评分
        results = _engine.score_batch(query, memories, top_k_val)
        
        return json.dumps({
            "results": results,
            "count": len(results),
            "query": query
        }, ensure_ascii=False, default=str)
        
    except Exception as e:
        logger.error(f"记忆匹配失败: {e}")
        return json.dumps({"error": f"匹配失败: {str(e)}"}, ensure_ascii=False)


@ToolRegistry.register(
    name="memory_score",
    description="对单条记忆进行多维度匹配评分",
    params={"query": "查询文本", "memory_content": "记忆内容", "memory_timestamp": "记忆时间戳(ISO格式)", "memory_importance": "重要性(0-1)"},
    source="builtin"
)
def memory_score(
    query: str, 
    memory_content: str, 
    memory_timestamp: str = "", 
    memory_importance: str = "0.5"
) -> str:
    """
    对单条记忆进行多维度评分
    
    Args:
        query: 查询文本
        memory_content: 记忆内容
        memory_timestamp: 记忆时间戳（ISO 格式），可选
        memory_importance: 重要性（0-1），默认 0.5
        
    Returns:
        JSON 格式的评分明细
    """
    try:
        # 构建记忆字典
        try:
            importance_val = float(memory_importance)
        except ValueError:
            importance_val = 0.5
        
        memory = {
            "content": memory_content,
            "timestamp": memory_timestamp,
            "importance": importance_val
        }
        
        # 调用引擎评分
        result = _engine.score_single(query, memory)
        
        # 移除 memory 字段（单条评分不需要返回原始数据）
        result.pop("memory", None)
        result["query"] = query
        result["memory_content_preview"] = memory_content[:100] if len(memory_content) > 100 else memory_content
        
        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"记忆评分失败: {e}")
        return json.dumps({"error": f"评分失败: {str(e)}"}, ensure_ascii=False)


@ToolRegistry.register(
    name="memory_batch_filter",
    description="批量过滤记忆，只保留匹配分数超过阈值的记忆",
    params={"query": "查询文本", "memory_items": "JSON格式的记忆列表", "threshold": "最低分数阈值(0-1)，默认0.3"},
    source="builtin"
)
def memory_batch_filter(query: str, memory_items: str, threshold: str = "0.3") -> str:
    """
    批量过滤记忆，只保留匹配分数超过阈值的记忆
    
    Args:
        query: 查询文本
        memory_items: JSON 格式的记忆列表字符串，或直接传入列表
        threshold: 最低分数阈值（0-1），默认 0.3
        
    Returns:
        JSON 格式的过滤结果
    """
    try:
        # 解析 memory_items - 支持 JSON 字符串或列表
        if isinstance(memory_items, list):
            memories = memory_items
        else:
            try:
                memories = json.loads(memory_items)
            except (json.JSONDecodeError, TypeError) as e:
                return json.dumps({"error": f"JSON 解析失败: {str(e)}"}, ensure_ascii=False)
        
        if not isinstance(memories, list):
            return json.dumps({"error": "memory_items 必须是 JSON 数组"}, ensure_ascii=False)
        
        # 解析 threshold
        try:
            threshold_val = float(threshold)
            threshold_val = max(0.0, min(1.0, threshold_val))  # 限制在 0-1
        except ValueError:
            threshold_val = 0.3
        
        if not memories:
            return json.dumps({"results": [], "count": 0, "threshold": threshold_val}, ensure_ascii=False)
        
        # 批量评分
        all_results = _engine.score_batch(query, memories)
        
        # 过滤低于阈值的
        filtered_results = [r for r in all_results if r["total_score"] >= threshold_val]
        
        return json.dumps({
            "results": filtered_results,
            "count": len(filtered_results),
            "total": len(all_results),
            "threshold": threshold_val,
            "query": query
        }, ensure_ascii=False, default=str)
        
    except Exception as e:
        logger.error(f"记忆过滤失败: {e}")
        return json.dumps({"error": f"过滤失败: {str(e)}"}, ensure_ascii=False)
