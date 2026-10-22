PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: help setup dev install uninstall test lint version

# 不带参数跑 make 时，列出能用的目标
help:
	@echo "make setup   装运行依赖（使用这套）"
	@echo "make dev     装开发依赖，含 ruff"
	@echo "make install 只装命令软链"
	@echo "make test    跑单元测试"
	@echo "make lint    跑 ruff 检查"
	@echo "make version 打印版本号"

# 一键安装：走 install.sh（没装 make 的机器也能直接跑脚本）
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
