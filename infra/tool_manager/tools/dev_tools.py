"""
开发工具包 — 代码解析、依赖管理、测试调试、代码质量
"""
import ast
import subprocess
import sys
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("dev_tools")

DEV_TIMEOUT = 60
MAX_OUTPUT = 30000


def _py_run(args: list, timeout: int = DEV_TIMEOUT, cwd: Optional[str] = None) -> Dict:
    try:
        r = subprocess.run([sys.executable, "-m"] + args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"stdout": (r.stdout or "")[:MAX_OUTPUT], "stderr": (r.stderr or "")[:MAX_OUTPUT], "exit_code": r.returncode, "success": r.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "success": False}
    except FileNotFoundError:
        return {"error": "模块未安装", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


# ── 代码解析 ──

@ToolRegistry.register("parse_ast", description="解析 Python 代码的抽象语法树，提取函数、类、变量信息。", params={"path": "Python 文件路径", "include_body": "可选，是否包含函数体（默认 False）"}, risk_level="LOW", category="query")
def parse_ast(path: str, include_body: bool = False) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        try: p = Path(__file__).resolve().parents[3] / path
        except Exception: return {"error": f"路径无法解析: {path}"}
    if not p.exists(): return {"error": f"文件不存在: {path}"}
    try:
        with open(p, encoding="utf-8") as f: source = f.read()
        tree = ast.parse(source)
        result = {"path": str(p), "functions": [], "classes": [], "imports": [], "total_lines": len(source.split("\n"))}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                fn = {"name": node.name, "lineno": node.lineno, "end_lineno": node.end_lineno,
                       "args": [a.arg for a in node.args.args], "decorators": [d.id if isinstance(d, ast.Name) else "" for d in node.decorator_list]}
                if node.returns: fn["return_annotation"] = ast.dump(node.returns) if hasattr(node.returns, 'id') else None
                if include_body: fn["body"] = ast.get_source_segment(source, node)
                result["functions"].append(fn)
            elif isinstance(node, ast.ClassDef):
                bases = [b.id if isinstance(b, ast.Name) else "" for b in node.bases]
                methods = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                result["classes"].append({"name": node.name, "lineno": node.lineno, "bases": bases, "methods": methods})
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", None)
                result["imports"].append({"module": module or "", "names": names, "lineno": node.lineno})
        return {"success": True, **result}
    except SyntaxError as e: return {"error": f"语法错误: {e}"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("find_definition", description="查找 Python 中函数或类的定义位置。", params={"name": "函数/类名", "path": "可选，限定搜索的目录"}, risk_level="LOW", category="query")
def find_definition(name: str, path: Optional[str] = None) -> Dict[str, Any]:
    if not name: return {"error": "名称不能为空"}
    search_root = Path(path).expanduser() if path else Path(__file__).resolve().parents[3]
    results = []
    for py_file in search_root.rglob("*.py"):
        if ".git" in py_file.parts or "__pycache__" in py_file.parts: continue
        try:
            with open(py_file, encoding="utf-8") as f: source = f.read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.name == name:
                    results.append({"file": str(py_file), "line": node.lineno, "type": "class" if isinstance(node, ast.ClassDef) else "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"})
        except Exception: continue
    return {"success": True, "count": len(results), "results": results[:20]}

@ToolRegistry.register("find_references", description="查找指定函数/类被引用的所有位置。", params={"name": "函数/类名", "path": "可选，限定搜索目录"}, risk_level="LOW", category="query")
def find_references(name: str, path: Optional[str] = None) -> Dict[str, Any]:
    if not name: return {"error": "名称不能为空"}
    search_root = Path(path).expanduser() if path else Path(__file__).resolve().parents[3]
    import subprocess
    try:
        r = subprocess.run(["grep", "-rn", f"\\b{name}\\b", str(search_root)], capture_output=True, text=True, timeout=30)
        lines = [l for l in (r.stdout or "").split("\n") if l.strip() and ".py:" in l]
        results = []
        for line in lines[:30]:
            parts = line.split(":", 2)
            if len(parts) >= 2: results.append({"file": parts[0], "line": parts[1], "content": parts[2] if len(parts) > 2 else ""})
        return {"success": True, "count": len(results), "results": results}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("get_function_signature", description="获取 Python 函数的参数列表和返回值类型。", params={"path": "文件路径", "function_name": "函数名"}, risk_level="LOW", category="query")
def get_function_signature(path: str, function_name: str) -> Dict[str, Any]:
    if not function_name: return {"error": "函数名不能为空"}
    p = Path(path).expanduser()
    if not p.is_absolute():
        try: p = Path(__file__).resolve().parents[3] / path
        except Exception: return {"error": f"路径无法解析: {path}"}
    if not p.exists(): return {"error": f"文件不存在: {path}"}
    try:
        with open(p, encoding="utf-8") as f: source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                args = []
                for a in node.args.args:
                    arg_info = {"name": a.arg}
                    if a.annotation: arg_info["type"] = ast.get_source_segment(source, a.annotation) if a.annotation else None
                    args.append(arg_info)
                returns = ast.get_source_segment(source, node.returns) if node.returns else None
                docstring = ast.get_docstring(node)
                return {"success": True, "function": function_name, "args": args, "returns": returns, "docstring": docstring, "lineno": node.lineno}
        return {"error": f"未找到函数 '{function_name}'"}
    except Exception as e: return {"error": str(e)}


# ── 依赖管理 ──

@ToolRegistry.register("check_dependency", description="检查 Python 依赖是否已安装。", params={"package": "包名"}, risk_level="LOW", category="query")
def check_dependency(package: str) -> Dict[str, Any]:
    if not package: return {"error": "包名不能为空"}
    try:
        import importlib.metadata
        ver = importlib.metadata.version(package)
        return {"installed": True, "package": package, "version": ver}
    except importlib.metadata.PackageNotFoundError:
        return {"installed": False, "package": package}

@ToolRegistry.register("install_dependency", description="从 PyPI 安装 Python 依赖。只允许安装 PyPI 包。", params={"package": "包名（可加版本号如 flask==2.0）", "upgrade": "可选，是否升级（默认 False）"}, risk_level="MEDIUM", category="admin", tags=["mutation"])
def install_dependency(package: str, upgrade: bool = False) -> Dict[str, Any]:
    import re as _re
    if not package: return {"error": "包名不能为空"}
    # 包名验证：只允许合法 PyPI 包名 + 可选版本约束
    if not _re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?([<>=!~]=?[a-zA-Z0-9._-]+)?$', package):
        return {"error": f"包名格式不合法: {package}。只允许字母、数字、点、连字符和版本约束。"}
    cmd = ["pip", "install"]
    if upgrade: cmd.append("--upgrade")
    cmd.append(package)
    try:
        r = subprocess.run([sys.executable, "-m"] + cmd, capture_output=True, text=True, timeout=120)
        return {"success": r.returncode == 0, "stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:] if r.stderr else "", "package": package, "installed": r.returncode == 0}
    except subprocess.TimeoutExpired: return {"error": "安装超时（120秒）"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("list_dependencies", description="列出项目所有 Python 依赖及其版本。", params={"path": "可选，指向 requirements.txt 或 pyproject.toml 所在目录"}, risk_level="LOW", category="query")
def list_dependencies(path: Optional[str] = None) -> Dict[str, Any]:
    import importlib.metadata as md
    try:
        dists = md.distributions()
        deps = sorted([{"name": d.metadata["Name"], "version": d.version} for d in dists if d.metadata.get("Name")], key=lambda x: x["name"].lower())
        return {"success": True, "count": len(deps), "dependencies": deps}
    except Exception as e: return {"error": str(e)}


# ── 测试与调试 ──

@ToolRegistry.register("run_pytest", description="运行 pytest 测试用例，返回测试结果报告。", params={"path": "可选，测试文件或目录路径", "verbose": "可选，是否详细输出（默认 True）", "args": "可选，额外参数"}, risk_level="MEDIUM", category="admin")
def run_pytest(path: Optional[str] = None, verbose: bool = True, args: Optional[str] = None) -> Dict[str, Any]:
    cmd = ["pytest"]
    if verbose: cmd.append("-v")
    if path: cmd.append(path)
    if args: cmd.extend(args.split())
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        stdout = (r.stdout or "")[:MAX_OUTPUT]
        stderr = (r.stderr or "")[:MAX_OUTPUT]
        passed = stdout.count("PASSED") if r.returncode == 0 else 0
        failed = stdout.count("FAILED")
        return {"success": r.returncode == 0, "stdout": stdout, "stderr": stderr, "passed": passed, "failed": failed, "exit_code": r.returncode}
    except subprocess.TimeoutExpired: return {"error": "测试超时（300秒）"}
    except FileNotFoundError: return {"error": "pytest 未安装"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("run_ruff", description="运行 Ruff 静态代码检查，返回错误和警告。", params={"path": "可选，文件或目录路径"}, risk_level="LOW", category="query")
def run_ruff(path: Optional[str] = None) -> Dict[str, Any]:
    cmd = ["ruff", "check"]
    if path: cmd.append(path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = (r.stdout or "")[:MAX_OUTPUT]
        issues = [l for l in output.split("\n") if l.strip() and ":" in l and l.split(":")[0].strip().endswith(".py")]
        return {"success": r.returncode == 0, "stdout": output, "stderr": (r.stderr or "")[:5000], "issues_count": len(issues)}
    except FileNotFoundError: return {"error": "ruff 未安装"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("run_black", description="运行 Black 代码格式化。检查模式默认不修改文件。", params={"path": "文件或目录路径", "check": "可选，仅检查不修改（默认 True）"}, risk_level="LOW", category="query")
def run_black(path: str, check: bool = True) -> Dict[str, Any]:
    if not path: return {"error": "路径不能为空"}
    cmd = ["black"]
    if check: cmd.append("--check")
    cmd.append(path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {"success": r.returncode == 0, "stdout": (r.stdout or "")[:MAX_OUTPUT], "stderr": (r.stderr or "")[:MAX_OUTPUT], "would_reformat": r.returncode != 0 and check}
    except FileNotFoundError: return {"error": "black 未安装"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("debug_code", description="单步调试 Python 代码：逐行执行并返回变量状态。", params={"code": "要调试的 Python 代码", "max_steps": "可选，最大步数（默认20）"}, risk_level="MEDIUM", category="admin")
def debug_code(code: str, max_steps: int = 20) -> Dict[str, Any]:
    if not code: return {"error": "代码不能为空"}
    max_steps = min(max_steps, 50)

    # 注入跟踪器
    trace_code = f"""import sys, traceback
class DebugTracer:
    def __init__(self): self.steps = []; self.max_steps = {max_steps}
    def trace(self, frame, event, arg):
        if len(self.steps) >= self.max_steps: return None
        if event == 'line':
            lineno = frame.f_lineno
            locals_copy = {{k: repr(v)[:100] for k, v in frame.f_locals.items() if not k.startswith('_')}}
            self.steps.append({{'line': lineno, 'event': event, 'locals': locals_copy}})
        return self.trace
tracer = DebugTracer()
sys.settrace(tracer.trace)
try:
{chr(10).join('    ' + l for l in code.split(chr(10)))}
except Exception as e:
    tracer.steps.append({{'line': -1, 'event': 'exception', 'error': str(e)}})
finally:
    sys.settrace(None)
import json
print('__DEBUG_RESULT__' + json.dumps(tracer.steps, ensure_ascii=False))
"""
    import tempfile, subprocess
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
    try:
        tmp.write(trace_code); tmp.close()
        r = subprocess.run([sys.executable, tmp.name], capture_output=True, text=True, timeout=30)
        output = r.stdout or ""
        import json
        if "__DEBUG_RESULT__" in output:
            data = json.loads(output.split("__DEBUG_RESULT__")[1].strip())
            return {"success": True, "steps": data, "exit_code": r.returncode}
        return {"success": True, "stdout": output[:MAX_OUTPUT], "stderr": (r.stderr or "")[:5000], "exit_code": r.returncode}
    except Exception as e: return {"error": str(e)}
    finally: os.unlink(tmp.name)


# ── 代码质量 ──

@ToolRegistry.register("calculate_cyclomatic_complexity", description="计算 Python 函数的圈复杂度。", params={"path": "Python 文件路径"}, risk_level="LOW", category="query")
def calculate_cyclomatic_complexity(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        try: p = Path(__file__).resolve().parents[3] / path
        except Exception: return {"error": f"路径无法解析: {path}"}
    if not p.exists(): return {"error": f"文件不存在: {path}"}
    try:
        with open(p, encoding="utf-8") as f: source = f.read()
        tree = ast.parse(source)
        results = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler, ast.Assert, ast.BoolOp)):
                        complexity += 1
                    elif isinstance(child, ast.Try):
                        complexity += len(child.handlers)
                results.append({"name": node.name, "complexity": complexity, "lineno": node.lineno, "risk": "high" if complexity > 10 else "medium" if complexity > 5 else "low"})
        results.sort(key=lambda x: x["complexity"], reverse=True)
        return {"success": True, "file": str(p), "total_functions": len(results), "results": results}
    except SyntaxError as e: return {"error": f"语法错误: {e}"}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("detect_code_smells", description="检测 Python 代码中的坏味道（过长函数、过多参数、重复代码等）。", params={"path": "Python 文件路径"}, risk_level="LOW", category="query")
def detect_code_smells(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        try: p = Path(__file__).resolve().parents[3] / path
        except: return {"error": f"路径无法解析: {path}"}
    if not p.exists(): return {"error": f"文件不存在: {path}"}
    try:
        with open(p, encoding="utf-8") as f: source = f.read()
        tree = ast.parse(source)
        smells = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = (node.end_lineno or node.lineno) - node.lineno
                if lines > 50: smells.append({"type": "long_function", "name": node.name, "line": node.lineno, "detail": f"{lines} 行（建议<50）"})
                if len(node.args.args) > 5: smells.append({"type": "too_many_params", "name": node.name, "line": node.lineno, "detail": f"{len(node.args.args)} 个参数（建议<5）"})
                if not ast.get_docstring(node): smells.append({"type": "missing_docstring", "name": node.name, "line": node.lineno, "detail": "缺少文档字符串"})
            elif isinstance(node, ast.ClassDef):
                if not ast.get_docstring(node): smells.append({"type": "missing_docstring", "name": node.name, "line": node.lineno, "detail": "类缺少文档字符串"})
        return {"success": True, "file": str(p), "total_smells": len(smells), "smells": smells}
    except Exception as e: return {"error": str(e)}

@ToolRegistry.register("generate_documentation", description="自动生成 Python 函数和类的文档字符串。", params={"path": "Python 文件路径", "function_name": "可选，只生成指定函数的文档"}, risk_level="LOW", category="query")
def generate_documentation(path: str, function_name: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        try: p = Path(__file__).resolve().parents[3] / path
        except: return {"error": f"路径无法解析: {path}"}
    if not p.exists(): return {"error": f"文件不存在: {path}"}
    try:
        with open(p, encoding="utf-8") as f: source = f.read()
        tree = ast.parse(source)
        docs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if function_name and node.name != function_name: continue
                args_desc = "\n".join(f"        {a.arg}: 参数描述" for a in node.args.args)
                ret = "    Returns:\n        返回值描述" if node.returns else ""
                doc = f'    """{node.name} 函数\n\n    功能描述\n\n    Args:\n{args_desc}\n{ret}    """'
                docs.append({"name": node.name, "line": node.lineno, "generated_docstring": doc, "has_docstring": bool(ast.get_docstring(node))})
            elif isinstance(node, ast.ClassDef):
                if function_name and node.name != function_name: continue
                doc = f'    """{node.name} 类\n\n    功能描述\n\n    Attributes:\n        属性描述\n    """'
                docs.append({"name": node.name, "line": node.lineno, "generated_docstring": doc, "has_docstring": bool(ast.get_docstring(node))})
        return {"success": True, "file": str(p), "total": len(docs), "documentation": docs}
    except Exception as e: return {"error": str(e)}
