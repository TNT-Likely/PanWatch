#!/bin/bash
# ============================================================
# 同步 PanWatch 默认 AI 模型 → 预测引擎配置
# 用途: 预测引擎的 LLM 情绪打分跟随 PanWatch 设置页的默认模型
# 用法: bash sync_forecast_llm.sh
# 效果: 读 PanWatch providers API(默认模型) → 写 ~/.panwatch_forecast.env
#       (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY)
# ============================================================
set -e

PANWATCH_URL="${PANWATCH_URL:-http://localhost:8000}"
USERNAME="${AUTH_USERNAME:-admin}"
PASSWORD="${AUTH_PASSWORD:-admin123}"
ENV_FILE="${ENV_FILE:-$HOME/.panwatch_forecast.env}"

echo "▶ 登录 PanWatch..."
TOKEN=$(curl -s -X POST "$PANWATCH_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" | \
  python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["token"])' 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "❌ 登录失败,检查账号密码"
  exit 1
fi

echo "▶ 拉取默认 AI 模型..."
python3 - "$TOKEN" "$PANWATCH_URL" <<'PYEOF'
import sys, json, urllib.request, os

token, base = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    f"{base}/api/providers/services",
    headers={"Authorization": f"Bearer {token}"},
)
data = json.load(urllib.request.urlopen(req, timeout=10))
services = data.get("data", data) if isinstance(data, dict) else data

found = None
for svc in services:
    for m in svc.get("models", []):
        if m.get("is_default"):
            found = {"base_url": svc.get("base_url", ""), "model": m.get("model", ""), "api_key": svc.get("api_key", "")}
            break
    if found:
        break

if not found:
    print("❌ 未找到默认模型")
    sys.exit(1)

env_file = os.path.expanduser("~/.panwatch_forecast.env")
lines = []
if os.path.exists(env_file):
    lines = [l for l in open(env_file) if not l.strip().startswith("LLM_")]

lines.append(f"LLM_BASE_URL={found['base_url']}\n")
lines.append(f"LLM_MODEL={found['model']}\n")
lines.append(f"LLM_API_KEY={found['api_key']}\n")
with open(env_file, "w") as f:
    f.writelines(lines)
os.chmod(env_file, 0o600)

print(f"✅ 已同步: {found['model']} @ {found['base_url']}")
print(f"   写入 {env_file}")
PYEOF
