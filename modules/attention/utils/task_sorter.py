"""
任务优先级排序工具
"""


class TaskSorter:
    """任务排序器"""
    
    @staticmethod
    def sort_by_priority(tasks: list) -> list:
        """按优先级排序"""
        return sorted(tasks, key=lambda x: x.get('priority', 0), reverse=True)
