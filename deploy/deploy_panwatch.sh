#!/bin/bash
# ============================================================
# PanWatch 部署/重建恢复脚本
# 用途: 把 fork 仓库(xiaoze-hub/PanWatch)的自定义改动部署进容器
#       容器 docker rm + 重建后代码会丢(数据库配置在卷里保留),
#       运行本脚本即可恢复全部代码改动。
#
# 用法:
#   ./deploy_panwatch.sh          # 部署代码改动 + 重启
#   ./deploy_panwatch.sh --full   # 完整重建容器(含环境变量)
#   ./deploy_panwatch.sh --list   # 列出部署清单
#
# 前提:
#   - /tmp/PanWatch 是 xiaoze-hub/PanWatch 的 git clone(含全部改动)
#   - 容器名 panwatch, 数据卷 panwatch_data
# ============================================================
set -euo pipefail

REPO_DIR="${PANWATCH_REPO:-/tmp/PanWatch}"
CONTAINER="panwatch"
IMAGE="sunxiao0721/panwatch:latest"
TOKEN="${WUDAO_MCP_TOKEN:-}"
DOCKER="${SUDO:-sudo} docker"

# 加载配置文件(如果存在): panwatch.env 或 deploy_panwatch.env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for ENV_FILE in "$SCRIPT_DIR/panwatch.env" "$SCRIPT_DIR/deploy_panwatch.env"; do
  if [ -f "$ENV_FILE" ]; then
    echo "▶ 加载配置: $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    break
  fi
done

# 从配置(或环境变量)取值
CONTAINER="${PANWATCH_CONTAINER:-$CONTAINER}"
IMAGE="${PANWATCH_IMAGE:-$IMAGE}"
REPO_DIR="${PANWATCH_REPO:-$REPO_DIR}"
TOKEN="${WUDAO_MCP_TOKEN:-$TOKEN}"
AUTH_USERNAME="${AUTH_USERNAME:-admin}"
AUTH_PASSWORD="${AUTH_PASSWORD:-admin123}"

# 部署清单: 所有被改动的文件 (相对 REPO_DIR)
FILES=(
  "server.py"
  "src/web/api/chat.py"
  "src/agents/auction_review.py"
  "src/agents/theme_launch_detector.py"
  "src/agents/stock_attribution.py"
  "src/collectors/wudao_mcp_client.py"
  "src/collectors/market_sentiment_collector.py"
  "prompts/premarket_outlook.txt"
  "prompts/daily_report.txt"
  "src/web/api/forecast.py"
  "src/web/api/market.py"
  "src/web/api/recommendations.py"
  "src/web/api/discovery.py"
  "src/web/api/settings.py"
  "src/web/api/dashboard.py"
  "src/collectors/discovery_collector.py"
  "src/collectors/wudao_mcp_client.py"
  "src/core/strategy_engine.py"
  "src/core/entry_candidates.py"
  "src/core/sector_filter.py"
  "forecast_lib/forecast_sentiment.py"
  "deploy/sync_forecast_llm.sh"
  "src/agents/stock_attribution.py"
  "packages/marketdata/src/marketdata/vendors/ftshare.py"
  "packages/marketdata/src/marketdata/vendors/zhitu.py"
  "packages/marketdata/src/marketdata/registry.py"
)

echo "=============================================="
echo " PanWatch 部署脚本"
echo " 仓库: $REPO_DIR"
echo " 容器: $CONTAINER"
echo "=============================================="

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "❌ 仓库不存在: $REPO_DIR (先 git clone https://github.com/xiaoze-hub/PanWatch.git)"
  exit 1
fi

deploy_files() {
  echo "▶ 部署代码文件..."
  for f in "${FILES[@]}"; do
    if [ -f "$REPO_DIR/$f" ]; then
      $DOCKER cp "$REPO_DIR/$f" "$CONTAINER:/app/$f"
      echo "  ✅ $f"
    else
      echo "  ⚠️ 缺失: $f (跳过)"
    fi
  done
  # 容器内语法校验
  $DOCKER exec "$CONTAINER" python3 -c "
import ast, sys
for f in ['src/agents/auction_review.py','src/agents/theme_launch_detector.py','src/agents/stock_attribution.py','src/collectors/wudao_mcp_client.py','src/collectors/market_sentiment_collector.py','server.py','src/web/api/chat.py','src/web/api/recommendations.py','src/core/strategy_engine.py','src/core/entry_candidates.py']:
    try:
        ast.parse(open('/app/'+f).read())
    except Exception as e:
        print(f'语法错误 {f}: {e}'); sys.exit(1)
print('✅ 容器内语法校验通过')
" || { echo "❌ 语法校验失败"; exit 1; }
  # 前端构建 + 部署(改过 React 时必须,构建产物在 /app/static/)
  if [ -d "$REPO_DIR/frontend/node_modules/.bin" ] && [ -n "$(find "$REPO_DIR/frontend/src" "$REPO_DIR/frontend/packages" -name '*.tsx' -o -name '*.ts' -newer "$REPO_DIR/frontend/dist/index.html" 2>/dev/null | head -1)" ]; then
    echo "▶ 检测到前端源码更新,重新构建..."
    (cd "$REPO_DIR/frontend" && ./node_modules/.bin/vite build 2>&1 | tail -3) || { echo "❌ 前端构建失败"; exit 1; }
    $DOCKER cp "$REPO_DIR/frontend/dist/." "$CONTAINER":/app/static/ && echo "  ✅ 前端已部署到 /app/static/"
  else
    echo "  ⏭️ 前端无更新,跳过构建"
  fi
}

rebuild_container() {
  echo "▶ 重建容器(保留数据卷 $CONTAINER"_data")..."
  $DOCKER rm -f "$CONTAINER" 2>/dev/null || true
  local env_args=()
  if [ -n "$TOKEN" ]; then
    env_args+=(-e "WUDAO_MCP_TOKEN=$TOKEN")
  fi
  $DOCKER run -d \
    --name "$CONTAINER" \
    -p 8000:8000 \
    -v "${CONTAINER}_data:/app/data" \
    -e AUTH_USERNAME="$AUTH_USERNAME" \
    -e AUTH_PASSWORD="$AUTH_PASSWORD" \
    ${env_args[@]+"${env_args[@]}"} \
    -e TZ="Asia/Shanghai" \
    --memory=1g \
    --restart=unless-stopped \
    "$IMAGE"
  echo "  ✅ 容器已重建"
  sleep 12
}

# 主流程
case "${1:-}" in
  --list)
    echo "部署清单:"
    for f in "${FILES[@]}"; do
      [ -f "$REPO_DIR/$f" ] && echo "  ✅ $f" || echo "  ⚠️ $f"
    done
    ;;
  --full)
    rebuild_container
    deploy_files
    $DOCKER restart "$CONTAINER" >/dev/null
    echo "✅ 完整重建完成"
    ;;
  *)
    # 检查容器是否存在
    if $DOCKER ps -a --format '{{.Names}}' | grep -q "^$CONTAINER$"; then
      deploy_files
      $DOCKER restart "$CONTAINER" >/dev/null
      echo "✅ 代码已部署并重启"
    else
      echo "⚠️ 容器不存在,执行完整重建"
      rebuild_container
      deploy_files
      $DOCKER restart "$CONTAINER" >/dev/null
      echo "✅ 容器创建+代码部署完成"
    fi
    ;;
esac

sleep 10
echo ""
echo "▶ 健康检查:"
$DOCKER ps --format "{{.Names}} {{.Status}}" | grep "$CONTAINER" || echo "⚠️ 容器未运行"
curl -s -o /dev/null -w "  http://localhost:8000 → %{http_code}\n" http://localhost:8000/ || echo "  ⚠️ 服务未响应"
echo ""
echo "▶ 预测引擎检查:"
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8010/health 2>/dev/null | grep -q 200; then
  echo "  ✅ 预测引擎运行中 (:8010)"
else
  echo "  ⚠️ 预测引擎未运行,启动: sudo systemctl start panwatch-forecast"
  sudo systemctl start panwatch-forecast 2>/dev/null || true
fi
echo "▶ 同步 LLM 模型配置(设置页默认模型→引擎):"
bash ~/.hermes/scripts/sync_forecast_llm.sh 2>/dev/null | tail -1
echo ""
echo "▶ 主机预测引擎代码部署(从 git 拉最新):"
if [ -f "$REPO_DIR/forecast_server.py" ]; then
  cp "$REPO_DIR/forecast_server.py" /home/ubuntu/forecast_server.py
  mkdir -p /home/ubuntu/forecast_lib
  cp "$REPO_DIR"/forecast_lib/*.py /home/ubuntu/forecast_lib/ 2>/dev/null
  echo "  ✅ 引擎代码已同步,重启服务:"
  sudo systemctl restart panwatch-forecast 2>/dev/null || echo "  ⚠️ systemd 服务不存在,用 nohup 手动启动"
  echo ""
fi
echo "=============================================="
echo " ✅ 完成。数据卷保留账号: admin/admin123"
echo "    wudao token: $([ -n "$TOKEN" ] && echo '已配置(环境变量)' || echo '未配置,用 --full 时加 WUDAO_MCP_TOKEN=<token>')"
echo "=============================================="
