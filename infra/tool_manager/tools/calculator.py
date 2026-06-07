"""
计算器工具 - 数学运算
"""
import math
import operator
from typing import Union, List
from infra.tool_manager.tool_registry import ToolRegistry


def _get_memory():
    """延迟创建 MemoryManager，避免模块导入时的 I/O 开销"""
    from modules.memory.core.memory_manager import MemoryManager
    return MemoryManager()


ops = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "**": operator.pow,
    "%": operator.mod,
}


@ToolRegistry.register(
    "calc",
    description="简单计算，支持 + - * / ** %",
    params={"a": "左操作数", "op": "运算符", "b": "右操作数"},
    core=True,
)
def calculate(a: Union[int, float], op: str, b: Union[int, float]) -> str:
    """数学计算"""
    if op not in ops:
        return f"不支持的运算符: {op}"
    
    try:
        result = ops[op](float(a), float(b))
        if result == int(result):
            result = int(result)
        
        _get_memory().notebook_write_result(f"{a} {op} {b}", result)
        return f"{a} {op} {b} = {result}"
    except ZeroDivisionError:
        return "错误: 除数不能为零"
    except Exception as e:
        return f"计算错误: {str(e)}"


@ToolRegistry.register(
    "calc_advanced",
    description="高级计算，支持 sqrt, sin, cos, tan, log, exp",
    params={"func": "函数名", "value": "数值"}
)
def advanced_calc(func: str, value: Union[int, float]) -> str:
    """高级数学函数"""
    func = func.lower().strip()
    value = float(value)
    
    func_map = {
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
        "exp": math.exp,
        "abs": abs,
        "floor": math.floor,
        "ceil": math.ceil,
    }
    
    if func not in func_map:
        return f"不支持的函数: {func}，可用: {list(func_map.keys())}"
    
    try:
        result = func_map[func](value)
        if result == int(result):
            result = int(result)
        return f"{func}({value}) = {result}"
    except Exception as e:
        return f"计算错误: {str(e)}"


@ToolRegistry.register(
    "calc_sum",
    description="求和",
    params={"numbers": "数字列表，逗号分隔"}
)
def sum_numbers(numbers: str) -> str:
    """求和"""
    try:
        nums = [float(x.strip()) for x in numbers.split(",")]
        result = sum(nums)
        return f"sum({nums}) = {result}"
    except Exception as e:
        return f"解析错误: {str(e)}"


@ToolRegistry.register(
    "calc_avg",
    description="求平均值",
    params={"numbers": "数字列表，逗号分隔"}
)
def avg_numbers(numbers: str) -> str:
    """求平均"""
    try:
        nums = [float(x.strip()) for x in numbers.split(",")]
        result = sum(nums) / len(nums)
        return f"avg({nums}) = {result}"
    except Exception as e:
        return f"解析错误: {str(e)}"
