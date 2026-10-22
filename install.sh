#!/usr/bin/env bash
# ccs 一键安装。用法：
#   ./install.sh            装运行所需（不装 ruff 这类开发依赖）
#   ./install.sh --dev      连开发依赖一起装
#   ./install.sh --auto     没装 uv 时直接装，不询问
#   ./install.sh --no-link  只准备环境，不装命令软链
#   ./install.sh --no-deps  不装系统库，只打印手动命令（也可设 SKIP_DEPS=1）
set -eu

DEV=0
AUTO=0
LINK=1
NO_DEPS=0
if [ "${SKIP_DEPS:-0}" = "1" ]; then
    NO_DEPS=1
fi

for arg in "$@"; do
    case "$arg" in
        --dev|-d)   DEV=1 ;;
        --auto|-y)  AUTO=1 ;;
        --no-link)  LINK=0 ;;
        --no-deps)  NO_DEPS=1 ;;
        -h|--help)
            sed -n '2,7p' "$0"
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

# -------------------------------------------------------- 3.5 系统库
# 引擎是动态链接的 Firefox。精简的 Ubuntu / WSL 常缺 libasound，缺了会直接退出。
say "检查系统库..."
libs="$(PYTHONPATH="$here/src" .venv/bin/python - <<'PY'
from ccs.browser import deps
lack = deps.missing()
print(" ".join(lack))
print(" ".join(deps.packages(lack)))
PY
)"
lack="$(printf '%s\n' "$libs" | sed -n 1p)"
pkgs="$(printf '%s\n' "$libs" | sed -n 2p)"
if [ -n "$lack" ]; then
    say "缺少系统库: $lack"
    if ! command -v apt-get >/dev/null 2>&1; then
        say "不是 apt 系统，手动装上这些库：$pkgs"
    else
        # 提权顺序：root 直接跑；sudo 免密用 -n；
        # 有终端才用普通 sudo（可以问密码）；都不行就只打印命令。
        priv=""
        if [ "$EUID" -eq 0 ]; then
            priv=""
        elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
            priv="sudo -n"
        elif command -v sudo >/dev/null 2>&1 && [ -t 0 ]; then
            priv="sudo"
        fi
        if [ "$EUID" -eq 0 ]; then
            manual_prefix=""
        else
            manual_prefix="sudo "
        fi
        manual_update="${manual_prefix}apt-get -q update"
        manual_install="${manual_prefix}env DEBIAN_FRONTEND=noninteractive apt-get -y -q --no-install-recommends -o DPkg::Lock::Timeout=120 install $pkgs"
        exec_update="${priv:+$priv }apt-get -q update"
        exec_install="${priv:+$priv }env DEBIAN_FRONTEND=noninteractive apt-get -y -q --no-install-recommends -o DPkg::Lock::Timeout=120 install $pkgs"

        run_as_root() {
            case "$priv" in
                "sudo -n") sudo -n env DEBIAN_FRONTEND=noninteractive "$@" ;;
                "sudo") sudo env DEBIAN_FRONTEND=noninteractive "$@" ;;
                *) env DEBIAN_FRONTEND=noninteractive "$@" ;;
            esac
        }

        if [ "$NO_DEPS" = "1" ]; then
            say "已跳过安装（--no-deps 或 SKIP_DEPS=1）。手动执行："
            say "  $manual_update"
            say "  $manual_install"
        elif [ "$EUID" -ne 0 ] && [ -z "$priv" ]; then
            say "没有可用的 sudo，也不在终端里，不自动装。手动执行："
            say "  $manual_update"
            say "  $manual_install"
        else
            read -r -a pkg_list <<< "$pkgs"
            say "执行: $exec_update"
            if ! run_as_root apt-get -q update; then
                say "警告：apt-get update 失败，继续装库"
            fi
            say "执行: $exec_install"
            if ! run_as_root apt-get -y -q --no-install-recommends \
                -o DPkg::Lock::Timeout=120 install "${pkg_list[@]}"; then
                say "安装失败。手动执行："
                say "  $manual_update"
                say "  $manual_install"
            fi
        fi
    fi
else
    say "系统库齐了"
fi

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
