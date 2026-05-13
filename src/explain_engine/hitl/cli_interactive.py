"""HITL 1 — 多轮对话审 Bootstrap 出的 phenomena。

逐条 keep/edit/drop + 末尾 add 循环。
"""

from rich.console import Console
from rich.prompt import Confirm, Prompt

from explain_engine.schema.nodes import VariableNode


def review_phenomena(
    phenomena: list[VariableNode],
    console: Console | None = None,
) -> list[VariableNode]:
    """逐条 keep/edit/drop，末尾允许 add，返回最终保留 list。

    用户加的 phenomena id 用 p_user_NNN 前缀（NNN 从 001 起）。
    """
    console = console or Console()
    kept: list[VariableNode] = []

    for i, p in enumerate(phenomena, start=1):
        console.print(f"\n[bold cyan][{i}/{len(phenomena)}][/bold cyan] {p.name}")
        console.print(f"       {p.description}", style="dim")
        choice = Prompt.ask(
            "       [k]eep / [e]dit / [d]rop",
            choices=["k", "e", "d"],
            default="k",
        )
        if choice == "k":
            kept.append(p)
        elif choice == "e":
            new_name = Prompt.ask("       新名称", default=p.name)
            new_desc = Prompt.ask("       新描述", default=p.description)
            kept.append(p.model_copy(update={"name": new_name, "description": new_desc}))
        # d: 不加入 kept

    console.print(f"\n[INFO] 审查完成。当前保留 [bold]{len(kept)}[/bold] 条现象。")

    user_count = 0
    while Confirm.ask("要添加新的现象吗？", default=False):
        new_name_raw = Prompt.ask("新名称")
        new_name = new_name_raw.strip() if new_name_raw else ""
        if not new_name:
            console.print("[dim]名称为空，结束添加。[/dim]")
            break
        new_desc_raw = Prompt.ask("新描述")
        new_desc = new_desc_raw.strip() if new_desc_raw else ""
        user_count += 1
        kept.append(
            VariableNode(
                id=f"p_user_{user_count:03d}",
                name=new_name,
                description=new_desc or new_name,  # 描述空时退回名称
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )

    return kept
