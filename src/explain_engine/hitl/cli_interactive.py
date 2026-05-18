"""HITL 1 — 多轮对话审 Bootstrap 出的 phenomena。

逐条 keep/edit/drop + 末尾 add 循环。
"""

from collections.abc import Awaitable, Callable

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _next_p_id_num(kept: list[VariableNode]) -> int:
    """扫已有 p_NNN node 取 max+1。"""
    existing = [
        int(p.id[2:]) for p in kept
        if p.id.startswith("p_") and p.id[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


def review_phenomena(
    phenomena: list[VariableNode],
    console: Console | None = None,
) -> list[VariableNode]:
    """逐条 keep/edit/drop，末尾允许 add，返回最终保留 list。

    用户加的 phenomena id 用 p_NNN 顺延已存最大 +1，source="user" 标识。
    """
    console = console or Console()
    kept: list[VariableNode] = []

    for i, p in enumerate(phenomena, start=1):
        console.print(f"\n[bold cyan][{i}/{len(phenomena)}][/bold cyan] {p.name}")
        console.print(f"       {p.description}", style="dim")
        choice = Prompt.ask(
            r"       \[k]eep / \[e]dit / \[d]rop",
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

    while Confirm.ask("要添加新的现象吗？", default=False):
        new_name_raw = Prompt.ask("新名称")
        new_name = new_name_raw.strip() if new_name_raw else ""
        if not new_name:
            console.print("[dim]名称为空，结束添加。[/dim]")
            break
        new_desc_raw = Prompt.ask("新描述")
        new_desc = new_desc_raw.strip() if new_desc_raw else ""
        next_p_id = _next_p_id_num(kept)
        kept.append(
            VariableNode(
                id=f"p_{next_p_id:03d}",
                name=new_name,
                description=new_desc or new_name,  # 描述空时退回名称
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
                source="user",
            )
        )

    return kept


def review_insights(
    state: CognitiveState,
    console: Console | None = None,
) -> None:
    """HITL 2: 逐候选 keep/edit/drop/view-full。

    Phase 5: gain 列从 state.last_gains 读（EvaluationEngine 已持久化），
    不再要求 caller 传 gains dict —— 这样 stage=insight_pending 重入也能正确显示。

    Side effects:
        - drop: state.graph.remove_node(cid) 级联删 edges；state.last_gains.pop(cid)
        - edit: 改 candidate node.name/description + source="user"
        - 完成时: state.insight_candidates 清空
    """
    console = console or Console()

    # Step 1: 总览 table
    _render_insights_table(state, console)

    # Step 2: 逐候选
    candidates_snapshot = list(state.insight_candidates)
    for idx, cid in enumerate(candidates_snapshot, start=1):
        if cid not in state.graph.nodes:
            continue  # 防御性：已被前一步 drop
        cand = state.graph.nodes[cid]
        gain = state.last_gains.get(cid, 0.0)
        cov = _coverage_for(state, cid)
        console.print(
            f"\n[bold cyan][{idx}/{len(candidates_snapshot)}][/bold cyan] "
            f"{cid}  {cand.name}  (gain={gain:.2f})"
        )
        console.print(f"       描述: {cand.description}", style="dim")
        console.print(f"       覆盖 {len(cov)} 条 (默认收起)", style="dim")

        while True:
            choice = Prompt.ask(
                r"       \[k]eep / \[e]dit / \[d]rop / \[v]iew-full",
                choices=["k", "e", "d", "v"],
                default="k",
            )
            if choice == "k":
                break
            if choice == "d":
                state.graph.remove_node(cid)
                state.last_gains.pop(cid, None)
                break
            if choice == "e":
                new_name = Prompt.ask("       新名称", default=cand.name)
                new_desc = Prompt.ask("       新描述", default=cand.description)
                # 替换 node 的 name/description + 升级 source
                updated = cand.model_copy(
                    update={"name": new_name, "description": new_desc, "source": "user"}
                )
                state.graph.replace_node(cid, updated)
                break
            if choice == "v":
                for e in cov:
                    target = state.graph.nodes[e.target_node]
                    console.print(
                        f"         {target.id} {target.name}: {e.mechanism_description}",
                        style="dim",
                    )
                # 回到 prompt 重新选

    state.insight_candidates = []
    n_kept = sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1)
    if n_kept == 0:
        console.print(
            "\n[yellow][WARN] 未保留任何 insight，session 标为 done。"
            "可 explain new 重跑同问题。[/yellow]"
        )
    else:
        console.print(f"\n[green]已保留 {n_kept} 个 insight。[/green]")


def _render_insights_table(
    state: CognitiveState,
    console: Console,
) -> None:
    table = Table(title="候选 (按 compression_gain 降序)")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("描述", style="dim", max_width=40)
    table.add_column("Coverage", justify="right")
    table.add_column("Gain", justify="right", style="green")
    total = sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 0)
    for cid in state.insight_candidates:
        n = state.graph.nodes[cid]
        cov_count = len(_coverage_for(state, cid))
        table.add_row(
            cid, n.name, n.description,
            f"{cov_count}/{total}", f"{state.last_gains.get(cid, 0.0):.2f}",
        )
    console.print(table)


def _coverage_for(state: CognitiveState, cid: str) -> list[RelationEdge]:
    """返回该 candidate 的 outgoing manifests_as edges。"""
    return [
        e for e in state.graph.edges.values()
        if e.source_node == cid and e.relation_type == "manifests_as"
    ]


def review_predicted_l0(
    state: CognitiveState,
    predicted_L0_ids: list[str],
    *,
    console: Console | None = None,
) -> list[str]:
    """Wave B.2 HITL: 审 predicted L0. accept/reject/edit per L0, 返保留的 id list.

    Side effects:
      - reject 的 L0: 从 graph 删除 (cascade incident edges).
      - edit 的 L0: 改 name/description in place, source 升级为 "user".
      - accept: 不动.
    """
    if not predicted_L0_ids:
        return []
    cons = console or Console()
    kept: list[str] = []
    for pid in predicted_L0_ids:
        node = state.graph.nodes.get(pid)
        if not node:
            continue
        cons.print(
            f"\n[bold]{pid}[/bold] {node.name} — {node.description}"
        )
        choice = Prompt.ask(
            r"       \[a]ccept / \[r]eject / \[e]dit",
            choices=["a", "r", "e"],
            default="a",
        )
        if choice == "r":
            state.graph.remove_node(pid)
            cons.print(f"[yellow]rejected {pid}[/yellow]")
        elif choice == "e":
            new_name = Prompt.ask("       新 name", default=node.name)
            new_desc = Prompt.ask("       新 description", default=node.description)
            updated = node.model_copy(
                update={"name": new_name, "description": new_desc, "source": "user"}
            )
            state.graph.replace_node(pid, updated)
            kept.append(pid)
            cons.print(f"[green]edited {pid}[/green]")
        else:
            kept.append(pid)
    return kept


async def review_phenomena_async(
    phenomena: list[VariableNode],
    input_provider: Callable[[str], Awaitable[str]] | None,
    console: Console | None = None,
) -> list[VariableNode]:
    """Phase 11 Wave 2: async k/e/d 流 (REPL HITL 入口).

    跟 sync review_phenomena 行为对齐, 简化点:
    - edit 仅改 description (sync 同时改 name+description; async REPL 简流)
    - 加 'q' 中途取消 (返已 process 的 kept)
    - 无末尾 add 循环 (用户可 promote 后 /budget 等 slash 操作 graph)

    Args:
        phenomena: bootstrap 出的 L0 现象
        input_provider: REPL 端注入的 async input callable.
            None → 返全 phenomena (test / fallback path, accept all).
        console: Rich console (rendering); None 时新建

    Returns:
        list[VariableNode] — kept (按用户 k/e 决策保留, edit 用 model_copy 不变原)
    """
    if input_provider is None:
        return list(phenomena)

    if console is None:
        console = Console()

    kept: list[VariableNode] = []
    for i, p in enumerate(phenomena, start=1):
        console.print(
            f"\n[bold cyan][{i}/{len(phenomena)}][/bold cyan] {p.name}"
        )
        console.print(f"       {p.description}", style="dim")
        try:
            raw = await input_provider(
                "       [k]eep / [e]dit description / [d]rop / [q]uit (默认 k): "
            )
        except (EOFError, KeyboardInterrupt):
            console.print("[yellow]取消 HITL review (保留已选 phenomena).[/yellow]")
            break
        action = raw.strip().lower()

        if action in ("q", "quit"):
            console.print("[yellow]取消 HITL review.[/yellow]")
            break
        if action == "d":
            continue
        if action == "e":
            try:
                new_desc_raw = await input_provider(
                    "       新 description (回车保持原值): "
                )
            except (EOFError, KeyboardInterrupt):
                kept.append(p)
                continue
            new_desc = new_desc_raw.strip()
            if new_desc:
                kept.append(p.model_copy(update={"description": new_desc}))
            else:
                kept.append(p)
            continue
        # default ('k' 或其他) → keep
        kept.append(p)

    return kept


async def review_insights_async(
    state: CognitiveState,
    input_provider: Callable[[str], Awaitable[str]] | None,
    console: Console | None = None,
) -> None:
    """Phase 11 Wave 2: async k/e/d 版 review_insights (REPL HITL 入口).

    跟 sync review_insights 行为对齐, 简化:
    - 无 'v' view-full sub-prompt (Rich table 总览仍渲染, 用户可 promote 后看)
    - edit 仅改 description (sync 改 name+description)
    - 加 'q' 中途取消 (剩余 candidates 留 graph 中, 不清空 insight_candidates)

    Side effects (同 sync):
    - drop: state.graph.remove_node(cid) 级联删 edges; state.last_gains.pop(cid)
    - edit: 改 candidate node.description + source="user"
    - 完成: state.insight_candidates = []
    - 取消 (q): state.insight_candidates 保持 (待用户下次 review)

    Args:
        state: 含 insight_candidates 的 CognitiveState (mutated in place)
        input_provider: REPL 端 async input callable.
            None → 不交互, 保留全 candidates, 清空 insight_candidates (accept all).
        console: Rich console; None 时新建
    """
    if console is None:
        console = Console()

    _render_insights_table(state, console)

    if input_provider is None:
        # accept-all path: 全 keep + 清 candidates (跟 sync 完成态一致)
        state.insight_candidates = []
        return

    candidates_snapshot = list(state.insight_candidates)
    cancelled = False
    for idx, cid in enumerate(candidates_snapshot, start=1):
        if cid not in state.graph.nodes:
            continue  # 已被前一步 drop
        cand = state.graph.nodes[cid]
        gain = state.last_gains.get(cid, 0.0)
        cov = _coverage_for(state, cid)
        console.print(
            f"\n[bold cyan][{idx}/{len(candidates_snapshot)}][/bold cyan] "
            f"{cid}  {cand.name}  (gain={gain:.2f})"
        )
        console.print(f"       描述: {cand.description}", style="dim")
        console.print(f"       覆盖 {len(cov)} 条", style="dim")

        try:
            raw = await input_provider(
                "       [k]eep / [e]dit description / [d]rop / [q]uit (默认 k): "
            )
        except (EOFError, KeyboardInterrupt):
            console.print("[yellow]取消 HITL review.[/yellow]")
            cancelled = True
            break
        action = raw.strip().lower()

        if action in ("q", "quit"):
            console.print("[yellow]取消 HITL review.[/yellow]")
            cancelled = True
            break
        if action == "d":
            state.graph.remove_node(cid)
            state.last_gains.pop(cid, None)
            continue
        if action == "e":
            try:
                new_desc_raw = await input_provider(
                    "       新 description (回车保持原值): "
                )
            except (EOFError, KeyboardInterrupt):
                continue
            new_desc = new_desc_raw.strip()
            if new_desc:
                updated = cand.model_copy(
                    update={"description": new_desc, "source": "user"}
                )
                state.graph.replace_node(cid, updated)
            continue
        # default ('k' 或其他) → keep

    if not cancelled:
        state.insight_candidates = []
        n_kept = sum(
            1 for n in state.graph.nodes.values() if n.abstraction_level == 1
        )
        if n_kept == 0:
            console.print(
                "\n[yellow][WARN] 未保留任何 insight, session 标为 done. "
                "可 explain new 重跑同问题.[/yellow]"
            )
        else:
            console.print(f"\n[green]已保留 {n_kept} 个 insight.[/green]")
