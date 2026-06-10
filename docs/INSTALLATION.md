# 安装指南

针对不同操作系统的详细安装说明。

## Windows

### 方式一：一键安装（推荐）

在 PowerShell 中执行（右键 → 以 PowerShell 身份运行）：

```powershell
powershell -ExecutionPolicy Bypass -Command "iex (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/15087312/cortex_agent/main/install.ps1')"
```

### 方式二：手动安装

**前置条件**：
- [Git for Windows](https://git-scm.com/download/win)
- [Python 3.11+](https://www.python.org/downloads/)

**步骤**：

1. 克隆仓库
```powershell
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent
```

2. 创建虚拟环境（可选但推荐）
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. 安装依赖
```powershell
pip install -e .
```

4. 配置环境变量
```powershell
Copy-Item .env.example .env
# 用文本编辑器编辑 .env，填入你的 API Key
```

5. 启动
```powershell
cortex
```

> **注意**：如果 `cortex` 命令未找到，关闭并重新打开 PowerShell（刷新 PATH），或使用 `python -m cortex.main`

### 常见问题

**Q: PowerShell 提示"无法加载脚本"？**

A: 运行以下命令设置执行策略：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Q: 找不到 python 命令？**

A: 确保 Python 安装时勾选了"Add Python to PATH"，或使用完整路径：
```powershell
C:\Users\YourUsername\AppData\Local\Programs\Python\Python311\python.exe -m cortex.main
```

**Q: 如何在 WSL 中安装？**

A: 在 WSL 终端中执行 macOS/Linux 的安装命令。

---

## macOS

### 方式一：一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

### 方式二：Homebrew（待支持）

```bash
brew install cortex-agent
```

### 方式三：手动安装

```bash
# 1. 克隆
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent

# 2. 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -e .

# 4. 配置
cp .env.example .env
# 编辑 .env 填入 API Key

# 5. 启动
cortex
```

### Apple Silicon (M1/M2/M3)

大部分依赖已原生支持，少数需要编译的依赖可能较慢：

```bash
# 可选：使用 conda 加速（支持 arm64 wheels）
conda create -n cortex python=3.13
conda activate cortex
pip install -e .
```

---

## Linux

### 方式一：一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

### 方式二：手动安装

**Ubuntu / Debian**：

```bash
# 1. 安装系统依赖
sudo apt update
sudo apt install -y python3.13 python3.13-venv git build-essential

# 2. 克隆
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent

# 3. 创建虚拟环境
python3.13 -m venv venv
source venv/bin/activate

# 4. 安装依赖
pip install -e .

# 5. 配置
cp .env.example .env
# 编辑 .env 填入 API Key

# 6. 启动
cortex
```

**CentOS / RHEL**：

```bash
# 1. 安装系统依赖
sudo yum install -y python313 python313-devel git gcc

# 2-6. 同上（用 python3.13 替换 python3.13）
```

### Docker（推荐用于服务器）

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止
docker-compose down
```

---

## 验证安装

安装完成后验证：

```bash
# 查看版本
cortex --version

# 健康检查
curl http://127.0.0.1:8080/health
```

预期输出：

```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

---

## 疑难排除

### 问题：无法下载脚本

**原因**：网络问题或 GitHub 不可达

**解决**：
1. 检查网络连接
2. 使用代理：`https_proxy=xxx curl ...`
3. 手动下载脚本后执行：
   - Windows: 保存 install.ps1，`.\install.ps1`
   - macOS/Linux: 保存 install.sh，`bash install.sh`

### 问题：pip 权限错误

**原因**：试图在系统 Python 中安装

**解决**：使用虚拟环境或添加 `--user` 标志：
```bash
pip install --user -e .
```

### 问题：模型 API 连接失败

**原因**：API Key 错误或网络不可达

**排查**：
```bash
# 测试 API 连接
curl -X GET http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}'
```

### 问题：GPU 加速未启用

**原因**：依赖未正确安装或 CUDA 驱动不兼容

**解决**：
```bash
# 检查 PyTorch
python -c "import torch; print(torch.cuda.is_available())"

# 重新安装 GPU 版本
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

---

## 更新

### 一键更新

```bash
# macOS / Linux
cd ~/cortex_agent && git pull && pip install -e .

# Windows PowerShell
cd $HOME\cortex_agent; git pull; pip install -e .
```

### Docker 更新

```bash
docker-compose pull
docker-compose up -d
```

---

## 卸载

### Python 包卸载

```bash
pip uninstall cortex-agent
```

### 完全卸载（包括数据）

```bash
# macOS / Linux
rm -rf ~/cortex_agent

# Windows PowerShell
Remove-Item -Recurse -Force $HOME\cortex_agent
```

---

## 获取帮助

- 📖 [完整文档](../README.md)
- 🐛 [报告 Bug](https://github.com/15087312/cortex_agent/issues)
- 💬 [讨论问题](https://github.com/15087312/cortex_agent/discussions)
