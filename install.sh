#!/usr/bin/env bash
# ccs 一键安装。用法：
#   ./install.sh            装运行所需（不装 ruff 这类开发依赖）
#   ./install.sh --dev      连开发依赖一起装
#   ./install.sh --auto     没装 uv 时直接装，不询问
#   ./install.sh --no-link  只准备环境，不装命令软链
set -eu

DEV=0
AUTO=0
LINK=1

for arg in "$@"; do
    case "$arg" in
        --dev|-d)   DEV=1 ;;
        --auto|-y)  AUTO=1 ;;
        --no-link)  LINK=0 ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *)
            echo "不认识的参数: $arg" >&2
            exit 2
            ;;
    esac
done

here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- 1. uv
if ! command -v uv >/dev/null 2>&1; then
    install_uv() {
        say "安装 uv ..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    }
    if [ "$AUTO" = "1" ]; then
        install_uv
    else
        printf '缺少 uv（Python 包管理器）。现在装吗？[y/N] '
        read -r answer || answer=""
        case "$answer" in
            y|Y) install_uv ;;
            *)
                say "已取消。手动安装：https://docs.astral.sh/uv/getting-started/installation/"
                exit 1
                ;;
        esac
    fi
fi
command -v uv >/dev/null 2>&1 || { say "uv 装好了但当前 PATH 找不到，重开一个终端再试"; exit 1; }
say "uv: $(uv --version)"

# ------------------------------------------------------------ 2. 依赖
if [ "$DEV" = "1" ]; then
    say "安装依赖（含开发依赖）..."
    uv sync
else
    say "安装运行依赖..."
    uv sync --no-dev
fi

# ------------------------------------------------------ 3. 浏览器引擎
# 直接调 venv 里的 python：`uv run` 会顺手把开发依赖再装回来
say "检查浏览器引擎（首次约 260 MB）..."
.venv/bin/python -m invisible_playwright fetch

# ---------------------------------------------------------- 4. 命令软链
if [ "$LINK" = "1" ]; then
    prefix="${PREFIX:-$HOME/.local}"
    mkdir -p "$prefix/bin"
    ln -sfn "$here/ccs" "$prefix/bin/ccs"
    ln -sfn "$here/ccs" "$prefix/bin/claude-sandbox"
    say "已安装命令: $prefix/bin/ccs"
fi

# -------------------------------------------------------------- 5. 自检
say ""
./ccs doctor || true
say ""
say "下一步: 在 ~/.config/claude-sandbox/ccs.toml 里配好 exit，然后 ccs new <名字>"
