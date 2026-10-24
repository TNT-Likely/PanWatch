#!/bin/bash
set -euo pipefail

echo ">>> [1/5] 安装基础依赖..."
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

echo ">>> [2/5] 配置 Docker 官方源..."
sudo install -m 0755 -d /etc/apt/keyrings
if curl -fsSL --connect-timeout 10 https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg 2>/dev/null; then
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
else
    echo "提示: 连接 download.docker.com 超时，将直接使用 Debian 官方仓库安装 docker.io..."
fi

echo ">>> [3/5] 安装 Docker 引擎与 Compose 插件..."
sudo apt-get update
if ! sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null; then
    echo "使用 Debian 官方仓库安装 docker.io 与 docker-compose..."
    sudo apt-get install -y docker.io docker-compose
fi

echo ">>> [4/5] 启动 Docker 服务并设置开机自启..."
sudo systemctl enable --now docker

echo ">>> [5/5] 将用户 $USER 添加到 docker 组..."
sudo usermod -aG docker "$USER"

echo "=========================================="
echo "✅ Docker 安装与服务配置已成功完成！"
echo "请执行 'newgrp docker' 刷新用户组权限，然后执行:"
echo "    docker compose up -d"
echo "=========================================="
