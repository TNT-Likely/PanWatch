.PHONY: help setup-backend dev-api dev-web build test test-notify eval doctor install-hooks clean-venv

# 端口约定：
#   - 后端：:8000（Docker / 本地 dev 统一，避免存量用户升级困惑）
#   - 前端：:5183（与 BeeCount-Cloud 的 :5173 错开避免冲突）

ifneq ($(filter Windows_NT,$(OS)),)
WINDOWS := 1
else ifneq (,$(findstring cmd.exe,$(ComSpec)))
WINDOWS := 1
endif

ifeq ($(WINDOWS),1)
SHELL := cmd.exe
.SHELLFLAGS := /C
PYTHON := python
VENV_PYTHON := .venv\Scripts\python.exe
else
PYTHON := python3
VENV_PYTHON := .venv/bin/python
endif

help:
	@echo "PanWatch 开发命令:"
	@echo "  make setup-backend   创建 venv 并安装后端依赖"
	@echo "  make dev-api         启动后端（:8000，自动 setup-backend）"
	@echo "  make dev-web         启动前端（:5183，自动 pnpm install）"
	@echo "  make test            跑全部单测（默认不发通知）"
	@echo "  make test-notify     跑全部单测（实际发送通知）"
	@echo "  make eval            跑 Agent 过程评测集（chat 用例需 EVAL_AI_* 环境变量）"
	@echo "  make doctor          系统自检(数据源/AI/通知/DB/磁盘/调度)"
	@echo "  make build VERSION=x 构建前端 + Docker 镜像"
	@echo "  make install-hooks   安装 git pre-push hook"
	@echo "  make clean-venv      删除本地 venv"

setup-backend:
ifeq ($(WINDOWS),1)
	@if not exist .venv ( echo >>> 创建 venv & $(PYTHON) -m venv .venv )
	@$(VENV_PYTHON) -m pip install -q -r requirements.txt
	@if not exist .env if exist .env.example copy /Y .env.example .env >nul
else
	@if [ ! -d .venv ]; then \
		echo ">>> 创建 venv"; \
		python3 -m venv .venv; \
	fi
	@$(VENV_PYTHON) -m pip install -q -r requirements.txt
	@if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
endif

# server.py 内部已经用 uvicorn.run(host=0.0.0.0, port=8000, reload=True) 启动。
dev-api: setup-backend
ifeq ($(WINDOWS),1)
	@set "DEV_RELOAD=1" && $(VENV_PYTHON) server.py
else
	@DEV_RELOAD=1 $(VENV_PYTHON) server.py
endif

dev-web:
ifeq ($(WINDOWS),1)
	@where pnpm >nul 2>&1 || ( echo pnpm 未安装，请先 npm install -g pnpm & exit /b 1 )
	@cd frontend && pnpm install --no-frozen-lockfile && pnpm dev
else
	@if ! command -v pnpm >/dev/null 2>&1; then \
		echo "pnpm 未安装，请先 npm install -g pnpm"; \
		exit 1; \
	fi
	cd frontend && pnpm install --no-frozen-lockfile && pnpm dev
endif

test:
	@$(VENV_PYTHON) -m pytest tests/ -v

test-notify:
	@$(VENV_PYTHON) -m pytest tests/ -v --notify

# Agent 过程评测(工具选择/参数/有据性/结构化输出/动作白名单):
#   - 结构化解析用例纯规则,直接跑
#   - chat 工具循环用例需被测模型: EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL
#   - 追加 LLM-as-judge: EVAL_JUDGE_* 环境变量 + EVAL_ARGS=--judge
#   - 建议在改动 prompts/*.txt 或工具 schema 后运行,低于阈值(EVAL_PASS_THRESHOLD)退出码非 0
eval:
	@$(VENV_PYTHON) tests/eval/run_eval.py $(EVAL_ARGS)

# 命令行系统自检:跑一遍数据源/AI/通知 + DB/磁盘/调度,打印结果与修复建议
doctor:
	@$(VENV_PYTHON) -m src.core.doctor

# 用法: make build VERSION=0.3.0
build:
ifeq ($(WINDOWS),1)
	@if "$(VERSION)"=="" ( echo Usage: make build VERSION=^<version^> & exit /b 1 )
	@powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -Version "$(VERSION)"
else
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make build VERSION=<version>"; \
		exit 1; \
	fi
	./build.sh $(VERSION)
endif

install-hooks:
	bash scripts/install-hooks.sh

clean-venv:
ifeq ($(WINDOWS),1)
	@if exist .venv rmdir /S /Q .venv
else
	rm -rf .venv
endif
