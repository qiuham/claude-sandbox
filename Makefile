PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: setup dev install uninstall test lint version

# 一键安装：走 install.sh（不依赖 make，没装 make 也能直接跑脚本）
setup:
	./install.sh

# 开发环境：连 ruff 一起装
dev:
	./install.sh --dev

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
