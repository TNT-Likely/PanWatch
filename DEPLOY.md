# PanWatch 部署文档(同事版)

镜像地址:**ghcr.io/xiaoze-hub/panwatch:latest**

> 包含所有定制(题材前瞻/事件驱动/简介标签/盘口扩展/zhitu/iTick 集成/实时指数等 51 个 commit)
> 数据源全在容器内自启,**只要给登录账号就能用**

## 快速启动(3 步)

### 1. 拉取镜像
```bash
sudo docker pull ghcr.io/xiaoze-hub/panwatch:latest
```

### 2. 创建配置(账号/token 可选)
```bash
mkdir -p ~/panwatch && cd ~/panwatch
cat > .env <<'EOF'
AUTH_USERNAME=admin
AUTH_PASSWORD=admin123
# 可选: 数据源 token(影响功能完整性)
ZHITU_TOKEN=your-zhitu-token
WUDAO_MCP_TOKEN=your-wudao-token
ITICK_TOKEN=your-itick-token
EOF
```

### 3. 启动容器
```bash
sudo docker run -d \
  --name panwatch \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file ~/panwatch/.env \
  -v panwatch_data:/app/data \
  ghcr.io/xiaoze-hub/panwatch:latest
```

打开浏览器 **http://localhost:8000** ,用 `.env` 里的账号密码登录。

## 完整版(推荐,带域名+HTTPS)

配合域名 `your-domain.com` 和 Caddy 反向代理:

```bash
sudo docker run -d \
  --name panwatch \
  --restart unless-stopped \
  -v panwatch_data:/app/data \
  --env-file ~/panwatch/.env \
  ghcr.io/xiaoze-hub/panwatch:latest
# 注意: 容器只监听 127.0.0.1:8000,需要 Caddy 反代并加 iptables 挡公网 8000
```

Caddyfile 片段(放到站点里):
```caddyfile
https://your-domain.com {
    tls {issuer acme {profile shortlived}}
    reverse_proxy 127.0.0.1:8000 {
        header_up Host {host}
        header_up X-Forwarded-Proto https
    }
}
```

## 数据持久化

所有数据(自选股/历史报告/AI 分析)存在 Docker 命名卷 `panwatch_data` 里。
**升级镜像** = 拉新镜像 + 重建容器(数据卷不动),**数据全在**:
```bash
sudo docker pull ghcr.io/xiaoze-hub/panwatch:latest
sudo docker stop panwatch && sudo docker rm panwatch
sudo docker run -d --name panwatch -p 8000:8000 --env-file ~/panwatch/.env -v panwatch_data:/app/data ghcr.io/xiaoze-hub/panwatch:latest
```

## 镜像版本

- `:latest` - 最新主分支(随仓库 main 滚动更新)
- `:0.1.0` / `:0.1.1` - 固定版本号(不会变,适合生产)

## 故障排查

- **拉不到镜像**: 检查 `sudo docker login` 状态(公开仓库免登录,但 Docker Hub 限流可能影响)
- **容器起不来**: `sudo docker logs panwatch`
- **页面 502**: 容器内 8000 端口未启动,等 30 秒(Gunicorn 启动慢)
- **功能受限**: 登录后看「设置 → 接口 Key」配置各数据源 token

## 系统要求

- 1GB+ 内存
- 5GB+ 磁盘(镜像本身 ~2GB)
- Docker 20.10+
