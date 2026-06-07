"""
安全工具包 — 静态安全扫描、危险代码检测
"""
import ast
import re
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("security_tools")


SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey|api_key)\s*[=:]\s*["\']([^"\']{8,})["\']', "API Key"),
    (r'(?i)(secret|token|password|passwd|pwd)\s*[=:]\s*["\']([^"\']{8,})["\']', "Secret/Token"),
    (r'(?i)(aws_access_key_id|aws_secret_access_key)\s*[=:]\s*["\']([^"\']+)["\']', "AWS Credential"),
    (r'(?i)(sk-[a-zA-Z0-9]{20,})', "OpenAI/DeepSeek Key"),
    (r'(?i)(ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9]{36,})', "GitHub Token"),
    (r'(?i)(-----BEGIN\s+(RSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----)', "Private Key"),
    (r'(?i)(mongodb(?:\+srv)?://[^\s"\']+)', "MongoDB URI"),
    (r'(?i)(postgresql?://[^\s"\']+:[^\s"\']+@[^\s"\']+)', "PostgreSQL URI"),
    (r'(?i)(redis://[^\s"\':]+:[^\s"\']+@[^\s"\']+)', "Redis URI"),
]

DANGEROUS_PATTERNS = [
    r'\beval\s*\(',
    r'\bexec\s*\(',
    r'\b__import__\s*\(',
    r'\bcompile\s*\(',
    r'\bos\.system\s*\(',
    r'\bsubprocess\.(call|Popen|run)\s*\(',
    r'\bshutil\.rmtree\s*\(',
    r'\bopen\s*\(\s*[\"\'](?:/|~)',
    r'\bpickle\.(load|loads)\s*\(',
    r'\binput\s*\(\s*(?!\s*\))',
    r'\bgetpass\.getpass\s*\(',
    r'\btempfile\.mktemp\s*\(',
    r'\bsqlite3\.connect\s*\(',
    r'\brequests?\.get\s*\(\s*[\"\']',
]


@ToolRegistry.register("scan_secrets", description="扫描代码文件中的硬编码密钥、密码、API Key 等敏感信息。", params={"path": "文件或目录路径"}, risk_level="LOW", category="query")
def scan_secrets(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists(): return {"error": f"路径不存在: {path}"}
    findings = []
    files = [p] if p.is_file() else list(p.rglob("*.py")) + list(p.rglob("*.env")) + list(p.rglob("*.yml")) + list(p.rglob("*.yaml")) + list(p.rglob("*.json")) + list(p.rglob("*.toml"))
    for f in files:
        if ".git" in f.parts or "__pycache__" in f.parts: continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for pattern, secret_type in SECRET_PATTERNS:
                for m in re.finditer(pattern, content):
                    findings.append({"file": str(f), "line": content[:m.start()].count("\n") + 1, "type": secret_type, "match": m.group(0)[:60]})
        except: continue
    return {"success": True, "total": len(findings), "findings": findings[:50]}


@ToolRegistry.register("scan_sast", description="静态应用安全测试：检测 SQL 注入、XSS、命令注入等漏洞模式。", params={"path": "文件或目录路径"}, risk_level="LOW", category="query")
def scan_sast(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists(): return {"error": f"路径不存在: {path}"}
    vulnerabilities = []
    patterns = [
        (r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*f[\"\']", "SQL Injection", "使用 f-string 拼接 SQL 查询"),
        (r"(?i)(SELECT|INSERT|UPDATE|DELETE)\s+.*\s*\+\s*(str|user|request|input|get)", "SQL Injection", "SQL 查询拼接用户输入"),
        (r"os\.system\s*\(\s*f[\"\']", "Command Injection", "使用 f-string 执行系统命令"),
        (r"subprocess\.(call|Popen|run)\s*\(\s*f[\"\']", "Command Injection", "使用 f-string 执行子进程"),
        (r"(?i)(render_template_string|Markup|format\(f[\"\'])", "XSS", "未转义的模板渲染"),
        (r"eval\s*\(\s*request|eval\s*\(\s*input", "Code Injection", "对用户输入执行 eval"),
        (r"pickle\.(load|loads)\s*\(", "Insecure Deserialization", "不安全的反序列化"),
        (r"(?i)secret_key\s*=\s*[\"\'][a-zA-Z0-9]{1,16}[\"\']", "Weak Secret Key", "密钥过短"),
    ]
    files = [p] if p.is_file() else list(p.rglob("*.py"))
    for f in files:
        if ".git" in f.parts or "__pycache__" in f.parts: continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for pat, vuln_type, desc in patterns:
                for m in re.finditer(pat, content):
                    vulnerabilities.append({"file": str(f), "line": content[:m.start()].count("\n") + 1, "type": vuln_type, "description": desc, "match": m.group(0)[:80]})
        except: continue
    return {"success": True, "total": len(vulnerabilities), "vulnerabilities": vulnerabilities[:50]}


@ToolRegistry.register("scan_dangerous_code", description="扫描检测危险代码模式（eval、exec、system 调用等）。", params={"path": "文件或目录路径"}, risk_level="LOW", category="query")
def scan_dangerous_code(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists(): return {"error": f"路径不存在: {path}"}
    findings = []
    files = [p] if p.is_file() else list(p.rglob("*.py"))
    for f in files:
        if ".git" in f.parts or "__pycache__" in f.parts: continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for pat in DANGEROUS_PATTERNS:
                for m in re.finditer(pat, content):
                    findings.append({"file": str(f), "line": content[:m.start()].count("\n") + 1, "pattern": m.group(0)[:60]})
        except: continue
    return {"success": True, "total": len(findings), "findings": findings[:50]}


@ToolRegistry.register("scan_dependencies", description="扫描已安装依赖的安全漏洞（使用 pip-audit 或 safety）。", params={}, risk_level="LOW", category="query")
def scan_dependencies() -> Dict[str, Any]:
    # 尝试 pip-audit
    for tool in [["pip-audit"], [sys.executable, "-m", "pip_audit"], ["safety", "check"]]:
        try:
            r = subprocess.run(tool, capture_output=True, text=True, timeout=120)
            if r.returncode != 127:
                return {"success": True, "tool": tool[0], "stdout": r.stdout[:10000], "stderr": r.stderr[:5000], "vulnerable": r.returncode != 0}
        except: continue
    return {"error": "未安装 pip-audit 或 safety，请先安装 (pip install pip-audit)"}


import sys  # noqa: E402
