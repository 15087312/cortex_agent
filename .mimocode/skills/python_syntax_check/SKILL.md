---
name: python_syntax_check
description: "检查Python文件语法正确性，确保代码可以正常编译"
keywords:
  - python语法检查
  - py_compile
  - 语法错误
  - 代码编译
  - python检查
  - 语法验证
  - 代码质量
---

# Python 语法检查技能

## 概述
检查Python文件的语法正确性，确保代码可以正常编译，避免语法错误导致的运行时问题。

## 使用场景
- 修改代码后检查语法
- 提交前验证代码质量
- 调试时排除语法问题
- 批量检查多个文件

## 工作流程

### 步骤 1: 检查单个文件
```bash
python3 -m py_compile <文件路径> && echo "✅ 语法检查通过"
```
或使用相对路径：
```bash
python3 -m py_compile modules/thinking/core/continuous_thinker.py && echo "✅ 语法检查通过"
```

### 步骤 2: 检查多个文件
```bash
python3 -m py_compile file1.py && python3 -m py_compile file2.py && echo "✅ 所有文件语法检查通过"
```

### 步骤 3: 批量检查目录
```bash
find . -name "*.py" -exec python3 -m py_compile {} \;
```

### 步骤 4: 带错误输出的检查
```bash
python3 -m py_compile <文件路径> 2>&1 | head -20
```

## 常见错误类型

### SyntaxError
- 缩进错误
- 缺少冒号
- 括号不匹配
- 字符串引号问题

### IndentationError
- 混用空格和制表符
- 缩进层级错误

### TabError
- 制表符和空格混用

## 输出格式

```
## Python 语法检查报告

### 检查文件
- modules/thinking/core/continuous_thinker.py
- modules/thinking/core/model_runner.py

### 检查结果
✅ modules/thinking/core/continuous_thinker.py - 语法正确
✅ modules/thinking/core/model_runner.py - 语法正确

### 总结
- 检查文件数: 2
- 通过: 2
- 失败: 0
```

## 最佳实践

1. **修改后立即检查**: 每次修改代码后立即运行语法检查
2. **提交前检查**: 在git commit前确保所有修改的文件语法正确
3. **批量检查**: 对大量修改使用批量检查提高效率
4. **查看详细错误**: 遇到错误时使用 `2>&1` 查看详细信息

## 注意事项
- 语法检查只能发现语法错误，不能发现逻辑错误
- 某些错误可能需要运行时才能发现
- 建议结合单元测试使用
