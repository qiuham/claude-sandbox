"""终端富文本输出：表格、颜色、截断。

Rich 是可选依赖：装了就用表格，没装就退回 report.py 生成的纯文本行。
和 cca 用同一个钉住的版本，避免两个工具在同一台机器上打架。
"""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.table import Table

RICH_VERSION = "15.0.0"


def rich_available() -> bool:
    """装着钉住版本的 Rich 吗？不抛错，供探路用。"""
    try:
        return metadata.version("rich") == RICH_VERSION
    except Exception:  # noqa: BLE001 - 没有就是没有
        return False


def rich_version() -> str:
    """装着钉住版本的 Rich 就返回版本号，否则空串。"""
    try:
        return metadata.version("rich") if rich_available() else ""
    except Exception:  # noqa: BLE001 - 没装就是空
        return ""


def _console():
    from rich.console import Console  # noqa: PLC0415 - 只在真正渲染时导入

    return Console()


def emit_table(headers: list[str], rows: list[list[str]], *,
               title: str = "", fallback: list[str],
               styles: list[list[str]] | None = None) -> None:
    """有 Rich 就画表格，没有就逐行打印 fallback。"""
    if not rich_available():
        for line in fallback:
            print(line)  # noqa: T201 - CLI 输出边界
        return

    table: Table = _table(title)
    for header in headers:
        # 每列都给 min_width：只给第一列的话，Rich 会把第一列挤成 0 宽直接消失
        table.add_column(header, overflow="ellipsis", no_wrap=True, min_width=4)
    for index, row in enumerate(rows):
        row_styles = styles[index] if styles and index < len(styles) else []
        table.add_row(*[_cell(value, row_styles[col] if col < len(row_styles) else "")
                        for col, value in enumerate(row)])
    _console().print(table)


def _cell(value: str, style: str):
    """一律包成 Text：直接传 str 会被 Rich 当成标记解析，
    节点名里的 [vless] 这类方括号会被吃掉。"""
    from rich.text import Text  # noqa: PLC0415 - 只在真正渲染时导入

    return Text(value, style=style or None)


def _table(title: str) -> Table:
    from rich.table import Table  # noqa: PLC0415 - 只在真正渲染时导入

    return Table(title=title or None, box=None, header_style="bold",
                 title_style="bold", pad_edge=False, show_edge=False)


def emit_pairs(pairs: list[tuple[str, str]], *, title: str = "", fallback: list[str],
               styles: list[str] | None = None) -> None:
    """两列（字段 : 值）的表格；值太长就在第二列折行，不截断。"""
    if not rich_available():
        for line in fallback:
            print(line)  # noqa: T201 - CLI 输出边界
        return

    table = _table(title)
    table.add_column("", style="dim", no_wrap=True)
    table.add_column("", overflow="fold")
    for index, (key, value) in enumerate(pairs):
        style = styles[index] if styles and index < len(styles) else ""
        table.add_row(key, _cell(value, style))
    _console().print(table)


def emit_note(label: str, value: str) -> None:
    """一行“标签 + 完整值”，不截断：sessionKey、长候选这种不适合放表格。"""
    emit_pairs([(label, value)], fallback=[kv(label, value)])


def _display_len(text: str) -> int:
    """标签占几列：中文算 2 列，和终端对齐用。"""
    try:
        from rich.cells import cell_len  # noqa: PLC0415 - 只在渲染时导入

        return cell_len(text)
    except Exception:  # noqa: BLE001 - 没装 Rich 就自己算
        from unicodedata import east_asian_width  # noqa: PLC0415

        return sum(2 if east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def kv(label: str, value: str, width: int = 10) -> str:
    """纯文本的“标签: 值”，标签按显示宽度补齐，冒号对齐。"""
    return f"{label}{' ' * max(1, width - _display_len(label))}: {value}"


def indent(value: str, width: int = 10) -> str:
    """续行：缩进到值那一列。"""
    return " " * (width + 2) + value


def emit_sections(sections: list[tuple[str, list[tuple[str, str]]]], *,
                  intro: str = "", footer: str = "") -> None:
    """分组的两列帮助（命令 + 说明），像 docker 那种。

    命令里有 `[名字…]` 这种方括号，所以一律走 Text，不让 Rich 当标记解析。
    纯文本兜底按显示宽度补齐：中文占两列，用字符数补会错位。
    """
    if not rich_available():
        _plain_sections(sections, intro=intro, footer=footer)
        return

    console = _console()
    if intro:
        console.print(intro, markup=False, highlight=False)
    for title, rows in sections:
        table = _table("")
        table.title = title
        table.title_justify = "left"
        table.title_style = "bold"
        table.pad_edge = False
        table.add_column("", no_wrap=True, min_width=4)
        table.add_column("", overflow="fold", style="dim")
        for command, note in rows:
            table.add_row(_cell(f"  {command}", ""), _cell(note, ""))
        console.print()
        console.print(table)
    if footer:
        console.print()
        console.print(footer, markup=False, highlight=False)


def _plain_sections(sections: list[tuple[str, list[tuple[str, str]]]], *,
                    intro: str, footer: str) -> None:
    width = max((_display_len(command) for _, rows in sections for command, _ in rows),
                default=0) + 2
    if intro:
        print(intro)  # noqa: T201 - CLI 输出边界
    for title, rows in sections:
        print(f"\n{title}")  # noqa: T201 - CLI 输出边界
        for command, note in rows:
            pad = " " * max(2, width - _display_len(command))
            print(f"  {command}{pad}{note}")  # noqa: T201 - CLI 输出边界
    if footer:
        print(f"\n{footer}")  # noqa: T201 - CLI 输出边界
