PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(CURDIR)

.PHONY: install uninstall test version

install:
	install -d $(BIN)
	ln -sfn $(REPO)/ccs $(BIN)/ccs
	ln -sfn $(REPO)/ccs $(BIN)/claude-sandbox
	@echo "已安装到 $(BIN): ccs, claude-sandbox"

uninstall:
	rm -f $(BIN)/ccs $(BIN)/claude-sandbox

test:
	.venv/bin/python -m unittest discover -s tests -v

version:
	@cat VERSION
