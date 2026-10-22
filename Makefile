PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: setup install uninstall test lint version

# 一键安装：uv 建环境 → 装依赖 → 下载浏览器引擎 → 装命令 → 自检
setup:
	@command -v uv >/dev/null || { \
		echo "缺少 uv，先装它：curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	uv sync
	uv run python -m invisible_playwright fetch
	$(MAKE) install
	@echo ""
	@./ccs doctor || true

install:
	install -d $(BIN)
	ln -sfn $(REPO)/ccs $(BIN)/ccs
	ln -sfn $(REPO)/ccs $(BIN)/claude-sandbox
	@echo "已安装到 $(BIN): ccs, claude-sandbox"

uninstall:
	rm -f $(BIN)/ccs $(BIN)/claude-sandbox

test:
	.venv/bin/python -m unittest discover -s tests -v

lint:
	uv run --frozen ruff check tests/ src/ ccs

version:
	@cat VERSION
