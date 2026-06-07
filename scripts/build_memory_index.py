"""
记忆索引构建脚本

用于批量构建长期记忆的向量索引。

用法:
    python scripts/build_memory_index.py                    # 构建所有类型索引
    python scripts/build_memory_index.py --type dialog      # 构建特定类型索引
    python scripts/build_memory_index.py --expert Memory   # 构建专家记忆索引
    python scripts/build_memory_index.py --rebuild         # 重建所有索引
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.memory.core.long_term import LongTermMemory
from utils.logger import setup_logger

logger = setup_logger("build_memory_index")


def build_all_indexes(ltm: LongTermMemory, memory_types: list = None):
    """构建所有类型的索引"""
    if memory_types is None:
        memory_types = ["dialog", "thought", "preference", "summary", "event"]

    total_stats = {"total": 0, "success": 0, "failed": 0}

    for mem_type in memory_types:
        logger.info(f"开始构建 {mem_type} 索引...")
        stats = ltm.build_index(memory_type=mem_type)
        total_stats["total"] += stats.get("total", 0)
        total_stats["success"] += stats.get("success", 0)
        total_stats["failed"] += stats.get("failed", 0)
        logger.info(f"{mem_type} 索引构建完成: {stats}")

    ltm.save_index()
    logger.info(f"所有索引构建完成: {total_stats}")

    return total_stats


def build_expert_indexes(ltm: LongTermMemory, expert_name: str = None):
    """构建专家记忆索引"""
    expert_names = ["Perception", "Memory", "Decision", "Planner", "Output",
                    "Reflection", "FactChecker", "ToolPreference", "Supervisor"]

    if expert_name:
        expert_names = [expert_name]

    total_stats = {"total": 0, "success": 0, "failed": 0}

    for expert in expert_names:
        logger.info(f"开始构建专家 {expert} 的索引...")

        from modules.memory.core.long_term import LongTermMemory
        from modules.memory.utils.embeddings import EmbeddingGenerator
        from modules.memory.utils.faiss_index import get_faiss_index_manager

        try:
            embeddings = EmbeddingGenerator()
            faiss_manager = get_faiss_index_manager()

            memories = ltm.load_expert_memory(expert, limit=5000)
            if not memories:
                logger.info(f"专家 {expert} 没有记忆，跳过")
                continue

            logger.info(f"专家 {expert} 有 {len(memories)} 条记忆")

            memory_types = set(m.get("type") for m in memories if m.get("type"))

            for mem_type in memory_types:
                type_memories = [m for m in memories if m.get("type") == mem_type]

                if not type_memories:
                    continue

                index = faiss_manager.create_expert_index(expert, mem_type, embeddings.embedding_dim)

                vectors = []
                mem_ids = []

                for mem in type_memories:
                    try:
                        content = mem.get("content", {})
                        if isinstance(content, dict):
                            text = content.get("text") or content.get("content") or str(content)
                        else:
                            text = str(content)

                        vector = embeddings.get_embedding(text)
                        if vector is not None:
                            vectors.append(vector)
                            mem_ids.append(mem["id"])

                    except Exception as e:
                        logger.warning(f"处理记忆失败: {e}")

                if vectors:
                    import numpy as np
                    index.add(np.array(vectors), mem_ids)
                    total_stats["success"] += len(vectors)

                total_stats["total"] += len(type_memories)

            index.save()
            logger.info(f"专家 {expert} 索引构建完成")

        except Exception as e:
            logger.error(f"构建专家 {expert} 索引失败: {e}")
            total_stats["failed"] += 1

    logger.info(f"专家索引构建完成: {total_stats}")
    return total_stats


def main():
    parser = argparse.ArgumentParser(description="构建记忆向量索引")
    parser.add_argument("--type", "-t", help="记忆类型 (dialog/thought/preference/summary/event)")
    parser.add_argument("--expert", "-e", help="专家名称")
    parser.add_argument("--all", "-a", action="store_true", help="构建所有索引（包括专家）")
    parser.add_argument("--rebuild", "-r", action="store_true", help="重建索引（先清空）")
    parser.add_argument("--data-dir", "-d", default="data/memory", help="数据目录")

    args = parser.parse_args()

    logger.info("开始构建记忆索引...")

    ltm = LongTermMemory(data_dir=f"{args.data_dir}/long_term")

    if args.rebuild:
        logger.info("重建模式：先清空现有索引")
        faiss_manager = None
        try:
            from modules.memory.utils.faiss_index import get_faiss_index_manager
            faiss_manager = get_faiss_index_manager()
        except Exception as e:
            logger.warning(f"无法清空索引: {e}")

    if args.expert:
        stats = build_expert_indexes(ltm, args.expert)
    elif args.type:
        stats = ltm.build_index(memory_type=args.type)
        ltm.save_index()
    elif args.all:
        build_all_indexes(ltm)
        build_expert_indexes(ltm)
    else:
        stats = build_all_indexes(ltm)

    logger.info("索引构建完成!")
    logger.info(f"统计: {stats}")


if __name__ == "__main__":
    main()
