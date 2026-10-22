PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: install uninstall test lint version

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
