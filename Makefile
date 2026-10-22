PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: setup install uninstall test lint version

# 一键安装：uv 建环境 → 装依赖 → 下载浏览器引擎 → 装命令 → 自检
setup:
	@if ! command -v uv >/dev/null; then \
		if [ "$(AUTO)" = "1" ]; then \
			echo "安装 uv ..."; \
			curl -LsSf https://astral.sh/uv/install.sh | sh || exit 1; \
		else \
			printf "缺少 uv（Python 包管理器）。现在装吗？[y/N] "; \
			read answer; \
			case "$$answer" in \
				y|Y) curl -LsSf https://astral.sh/uv/install.sh | sh || exit 1 ;; \
				*) echo "已取消。手动安装：https://docs.astral.sh/uv/getting-started/installation/"; exit 1 ;; \
			esac; \
		fi; \
	fi
	@export PATH="$$HOME/.local/bin:$$PATH"; uv sync
	@export PATH="$$HOME/.local/bin:$$PATH"; uv run python -m invisible_playwright fetch
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
