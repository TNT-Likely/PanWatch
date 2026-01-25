# PanWatch Dockerfile
# 多阶段构建，减小最终镜像大小

# ===== Stage 1: 前端构建 =====
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# 安装 pnpm
RUN npm install -g pnpm

# 复制依赖文件
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# 安装依赖
RUN pnpm install --frozen-lockfile

# 复制源码并构建
COPY frontend/ ./
RUN pnpm build


# ===== Stage 2: Python 运行环境 =====
FROM python:3.11-slim

# 版本号（构建时传入）
ARG VERSION=dev

WORKDIR /app

# 安装系统依赖
# - tzdata: 时区数据（zoneinfo 模块需要）
# - 中文字体（K线截图需要）
# - Playwright Chromium 依赖的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    # 中文字体
    fonts-noto-cjk \
    # Playwright Chromium 依赖
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 复制依赖文件
COPY requirements.txt ./

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 注意: Playwright 浏览器将在首次启动时自动安装到 data 目录
# 这样可以减小镜像体积，并支持跨版本持久化

# 复制后端代码
COPY src/ ./src/
COPY server.py ./
COPY prompts/ ./prompts/

# 写入版本号
RUN echo "${VERSION}" > VERSION

# 从前端构建阶段复制静态文件
COPY --from=frontend-builder /app/frontend/dist ./static/

# 创建数据目录
RUN mkdir -p /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# 暴露端口
EXPOSE 8000

# 健康检查（使用 Python）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "server.py"]
