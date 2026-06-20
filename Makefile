.PHONY: help setup-backend dev-api dev-web build test test-notify doctor install-hooks clean-venv

# 端口约定：
#   - 后端：:8000（Docker / 本地 dev 统一，避免存量用户升级困惑）
#   - 前端：:5183（与 BeeCount-Cloud 的 :5173 错开避免冲突）

help:
	@echo "PanWatch 开发命令:"
	@echo "  make setup-backend   创建 venv 并安装后端依赖"
	@echo "  make dev-api         启动后端（:8000，自动 setup-backend）"
	@echo "  make dev-web         启动前端（:5183，自动 pnpm install）"
	@echo "  make test            跑全部单测（默认不发通知）"
	@echo "  make test-notify     跑全部单测（实际发送通知）"
	@echo "  make doctor          系统自检(数据源/AI/通知/DB/磁盘/调度)"
	@echo "  make build VERSION=x 构建前端 + Docker 镜像"
	@echo "  make install-hooks   安装 git pre-push hook"
	@echo "  make clean-venv      删除本地 venv"

setup-backend:
	@if [ ! -d .venv ]; then \
		echo ">>> 创建 venv"; \
		python3 -m venv .venv; \
	fi
	@. .venv/bin/activate && pip install -q -r requirements.txt
	@if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi

# server.py 内部已经用 uvicorn.run(host=0.0.0.0, port=8000, reload=True) 启动。
dev-api: setup-backend
	. .venv/bin/activate && python server.py

dev-web:
	@if ! command -v pnpm >/dev/null 2>&1; then \
		echo "pnpm 未安装，请先 npm install -g pnpm"; \
		exit 1; \
	fi
	cd frontend && pnpm install --no-frozen-lockfile && pnpm dev

test:
	. .venv/bin/activate && python -m pytest tests/ -v

test-notify:
	. .venv/bin/activate && python -m pytest tests/ -v --notify

# 命令行系统自检:跑一遍数据源/AI/通知 + DB/磁盘/调度,打印结果与修复建议
doctor:
	. .venv/bin/activate && python -m src.core.doctor

# 用法: make build VERSION=0.3.0
build:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make build VERSION=<version>"; \
		exit 1; \
	fi
	./build.sh $(VERSION)

install-hooks:
	bash scripts/install-hooks.sh

clean-venv:
	rm -rf .venv
