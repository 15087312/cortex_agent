Humanoid AGI — Docker 部署
===============================

目录结构
  docker/
  ├── Dockerfile          # 后端多阶段构建（编译 → 脱源代码）
  ├── docker-compose.yml  # 一键启动后端服务
  ├── run.sh              # 一键运行（后端 + CLI）
  ├── pyproject.toml      # CLI 独立 pip 包配置
  └── .dockerignore       # 构建忽略规则


1. 前置要求
   - Docker Desktop (https://www.docker.com/products/docker-desktop/)
   - Python 3.9+
   - 在项目根目录配置 .env 文件（API Key 等）


2. 一键运行（推荐）
   cd <项目根目录>
   ./docker/run.sh

   自动完成：
   ① 构建后端 Docker 镜像（脱源代码）
   ② 启动后端服务（localhost:8080）
   ③ 创建 CLI 虚拟环境
   ④ 启动 Textual TUI


3. 仅启动后端
   cd <项目根目录>
   docker compose -f docker/docker-compose.yml up -d

   → 后端运行在 http://localhost:8080


4. 单独安装 CLI
   用户侧：
   pip install ai-backend-cli
   ai-backend --api-url http://your-server:8080

   （将 docker/pyproject.toml 放到项目根目录后也可本地安装：
   pip install -e .）


5. 源码保护说明
   Docker 采用多阶段构建：
   - builder 阶段：用 compileall 将 .py 编译为 .pyc
   - runtime 阶段：仅保留 .pyc（字节码），删除所有 .py 源文件
   - 图片中看不到后端 Python 源码，需借助工具才能反编译


6. 构建并分发
   # 构建镜像
   cd <项目根目录>
   docker compose -f docker/docker-compose.yml build backend

   # 打标签 & 推送到仓库
   docker tag humanoid-agi-backend:latest your-registry/humanoid-agi-backend:latest
   docker push your-registry/humanoid-agi-backend:latest

   # 用户拉取运行
   docker pull your-registry/humanoid-agi-backend:latest
   docker run -p 8080:8080 --env-file .env humanoid-agi-backend
