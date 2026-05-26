"""Explain Engine CLI 入口。

命令：
- explain new <question> — Bootstrap + HITL 1 + 落 session
- explain show <session_id> — 显示 session 内容
- explain list — 列出所有 session
- explain compress <session_id> — Compression + Evaluation + HITL 2

Phase 3 v0.1 / Phase 4。
"""

import asyncio
import logging

# Phase 9 chat input fix: 默认 input() 不开 readline, 中文多字节字符删除会有
# byte-level cursor 错位 (输入'你好' backspace 出现空格 / 字无法删).
# `import readline` 启用 GNU readline (Linux) / libedit (macOS) 让 input()
# 走 line-editing 模式, 大部分 multi-byte 删除问题立刻好.
# 注: macOS libedit Unicode 不完美, 如果还有问题, 后续可考虑 prompt_toolkit.
from datetime import datetime
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings, make_light_llm_client, make_llm_client
from explain_engine.engines._propagation import WEAK_CHAIN_THRESHOLD
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.engines.compression import propose_candidates
from explain_engine.engines.errors import InsufficientObservationsError
from explain_engine.engines.evaluation import score_all
from explain_engine.engines.input_validation import MIN_OVERLAP_SCORE
from explain_engine.engines.input_validation import validate as validate_input
from explain_engine.hitl.cli_interactive import review_insights, review_phenomena
from explain_engine.llm.errors import LLMError, SchemaValidationError
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

# Phase 7 Wave C 补丁: 让 score_all 等 engine 的 progress logger.info 显示出来
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

app = typer.Typer(
    help="Cognitive Engine for explanation-centric reasoning",
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()


@app.callback()
def main(ctx: typer.Context) -> None:
    """Phase 11 Wave 1: 无 subcommand 时进 ephemeral REPL; 传 subcommand 走老 cli.

    typer @app.callback fires before every subcommand. 通过 ctx.invoked_subcommand
    区分:
    - None: 用户跑 `explain` 没传 subcommand → enter_repl_async (Phase 11 默认)
    - 非 None: `explain list` 等 → 让 typer 跑 subcommand, callback 立即返回
    """
    if ctx.invoked_subcommand is None:
        from explain_engine.chat.repl_entry import enter_repl_async

        asyncio.run(enter_repl_async())
        raise typer.Exit()


def _get_store() -> SessionStore:
    # Phase 9: storage_v2 reads EXPLAIN_HOME / EXPLAIN_PROJECT_ID env vars directly
    return SessionStore()


@app.command()
def new(
    question: str = typer.Argument(..., help="为什么 X 问题"),
    no_chat: bool = typer.Option(
        False, "--no-chat",
        help="创建 session 后不进 chat REPL, 仅 print sid 退出 (脚本/CI 用)",
    ),
    lexicon_top_k: int = typer.Option(
        20, "--lexicon-top-k",
        help="bootstrap 拉 top-K lexicon var 作 prior (默认 20, 0 跳过)",
    ),
) -> None:
    """启动新 session: Bootstrap + HITL + (默认) 直接进 chat REPL.

    `--no-chat` 仅 print sid 退出 (脚本/CI 场景).

    Phase 11 Wave 2.5: 删除 tool budget cli flag — 改通过 chat 内 /budget
    slash interactive 配置.
    """
    asyncio.run(_run_new(question, no_chat, lexicon_top_k))


async def _run_new(
    question: str,
    no_chat: bool = False,
    lexicon_top_k: int = 20,
) -> None:
    settings = Settings()
    llm = make_llm_client()
    # Phase 17.2 Feature A: light_llm for classify (haiku / gpt-4o-mini).
    # LLM_LIGHT_* 未配则 fallback 主 LLM (= zero classify cost saving 但功能等价).
    light_llm = make_light_llm_client()

    # Phase 10 Wave 4: load lexicon prior (lexicon_top_k=0 跳过)
    from explain_engine.engines.lexicon import _load_lexicon
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    lexicon_path = storage.knowledge_dir() / "variables.json"
    lexicon_data = _load_lexicon(lexicon_path)
    lexicon = lexicon_data["variables"] if lexicon_top_k > 0 else None

    import os
    proto = os.environ.get("LLM_PROTOCOL", "?")
    model = os.environ.get("LLM_MODEL", "?")
    console.print(f"\n[INFO] 调 LLM ({proto} / {model}) 生现象...")
    try:
        phenomena = await bootstrap_phenomena(
            question, llm,
            lexicon=lexicon, lexicon_top_k=lexicon_top_k,
            light_llm=light_llm,
        )
    except SchemaValidationError as exc:
        console.print(f"[red]LLM 输出不合规: {exc}[/red]")
        raise typer.Exit(2) from exc
    except LLMError as exc:
        console.print(f"[red]LLM 调用失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[INFO] 生成 {len(phenomena)} 个现象，请审查。")
    final_phenomena = review_phenomena(phenomena, console=console)

    # 构造 session
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)

    store = _get_store()
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]session 保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc

    console.print(f"\n[green]Session {meta.session_id} 已保存。[/green]")

    if no_chat:
        console.print(f"       下一步：explain show {meta.session_id}")
        return

    # 默认: 直接进 chat REPL. 复用 cli.chat 的 _run_chat_repl_async (含
    # prompt_toolkit / log handler swap / 切换 logic). LLMClient 复用同 instance.
    console.print(
        "       进入 chat REPL (--no-chat 可跳过).\n"
    )
    await _run_chat_repl_async(
        initial_sid=meta.session_id,
        llm=llm,
    )


@app.command()
def show(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    trace: bool = typer.Option(False, "--trace", help="渲染 reasoning_trace 表 (Phase 5)"),
) -> None:
    """显示某个 session 的 phenomena 和 metadata。

    `--trace` 额外渲染 reasoning_trace (Phase 5 reasoning loop 每 tick 记录)。
    """
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Session {session.meta.session_id}[/bold]")
    console.print(f"问题: {session.meta.question}")
    console.print(f"Stage: {session.meta.stage}")
    console.print(f"现象数: {len(session.state.graph.nodes)}\n")

    table = Table(title="Phenomena")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("描述", style="dim")
    table.add_column("Level", justify="right")
    table.add_column("Epistemic")
    for nid, node in session.state.graph.nodes.items():
        table.add_row(
            nid,
            node.name,
            node.description,
            str(node.abstraction_level),
            node.epistemic,
        )
    console.print(table)

    if trace:
        if not session.state.reasoning_trace:
            console.print("\n[dim](reasoning_trace 为空)[/dim]")
        else:
            t = Table(title="Reasoning Trace")
            t.add_column("tick", justify="right")
            t.add_column("action")
            t.add_column("target")
            t.add_column("gain", justify="right")
            t.add_column("llm calls", justify="right")
            t.add_column("timestamp", style="dim")
            for e in session.state.reasoning_trace:
                t.add_row(
                    str(e.tick), e.action, e.target_node_id or "-",
                    f"{e.gain_delta:.2f}", str(e.llm_calls), e.timestamp,
                )
            console.print(t)


@app.command()
def compress(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """对已 bootstrap 的 session 跑 Compression + Evaluation + HITL 2。"""
    asyncio.run(_run_compress(session_id))


async def _run_compress(session_id: str) -> None:
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    stage = session.meta.stage
    if stage == "done":
        console.print(f"[red]session {session_id} 已 done，不可重跑。[/red]")
        raise typer.Exit(4)
    if stage not in ("bootstrap_pending", "insight_pending"):
        console.print(
            f"[red]session {session_id} stage={stage}，不支持 compress。[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()

    if stage == "bootstrap_pending":
        console.print("[INFO] 调 LLM 生成 abstract 候选...")
        try:
            from explain_engine.engines.lexicon import (
                get_lexicon_top_k_for_compress,
            )
            from explain_engine.persistence.storage_v2 import StorageV2
            top_k = get_lexicon_top_k_for_compress(StorageV2(), k=20)
            await propose_candidates(session.state, llm, existing_lexicon=top_k)
        except SchemaValidationError as exc:
            console.print(f"[red]LLM 输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        console.print(
            f"[INFO] 生成 {len(session.state.insight_candidates)} 个候选，开始评分..."
        )
        try:
            await score_all(session.state, llm)
        except SchemaValidationError as exc:
            console.print(f"[red]评分输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]评分 LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        session.meta.stage = "insight_pending"
        try:
            store.save(session)
            console.print("[INFO] 中间状态已保存 (stage=insight_pending)。")
        except OSError as exc:
            console.print(
                f"[red]中间状态保存失败（LLM 调用已消耗，重跑会再次计费）: {exc}[/red]"
            )
            raise typer.Exit(3) from exc
    else:  # stage == "insight_pending"
        console.print("[INFO] 检测到 stage=insight_pending，跳过 LLM 直接进入审查。")
        # Phase 5: gain 已持久化进 state.last_gains，重入也能正确显示

    # HITL 2 — Phase 5 起从 state.last_gains 读
    review_insights(session.state, console=console)

    session.meta.stage = "done"
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc

    # Phase 10 Wave 4: session done 触发 flush_to_lexicon (best-effort,
    # exception 不 fail 整个 compress — lexicon 是 supplementary store).
    from explain_engine.engines.lexicon import flush_to_lexicon
    from explain_engine.persistence.storage_v2 import StorageV2
    try:
        n = await flush_to_lexicon(session, StorageV2(), llm=llm)
        if n > 0:
            console.print(f"[INFO] {n} var 写入 lexicon")
    except Exception as exc:
        console.print(f"[yellow]lexicon flush 失败 (非关键): {type(exc).__name__}: {exc}[/yellow]")

    console.print(f"\n[green]Session {session_id} 已完成。[/green]")


@app.command()
def run(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    budget: int = typer.Option(15, "--budget", help="reasoning loop tick 上限"),
    no_input_check: bool = typer.Option(
        False, "--no-input-check",
        help="Phase 8 Wave 3: 跳过 input validation fail-fast (兜底用)",
    ),
) -> None:
    """Phase 5 reasoning loop: 上溯 driver,自动收敛。

    session 必须 stage=done (Phase 4 HITL 2 完成后)。
    跑完 stage 变 converged。

    Phase 8 Wave 3: tick=0 时调 input_validation, overlap_score < MIN_OVERLAP_SCORE
    fail-fast (exit 2). 用 --no-input-check 跳过.
    """
    from explain_engine.runtime.runtime import run as runtime_run

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if session.meta.stage != "done":
        console.print(
            f"[red]session stage={session.meta.stage!r}, must be 'done' to run "
            f"(先跑 explain compress)。[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()

    # ── Wave 3 Phase 8: input validation (tick=0 一次性 fail-fast) ──
    # design §9.4 可证伪性: 系统必须能说"我无法回答这个 question",
    # 而非强行编造 explanation. tick > 0 (resume) 跳过, --no-input-check 跳过.
    if not no_input_check and session.state.tick == 0:
        l0_nodes = [
            n for n in session.state.graph.nodes.values()
            if n.abstraction_level == 0
        ]
        try:
            align_report = asyncio.run(
                validate_input(session.state.root_question, l0_nodes, llm)
            )
            session.state.last_input_alignment_report = align_report
            if align_report.overlap_score < MIN_OVERLAP_SCORE:
                raise InsufficientObservationsError(
                    overlap_score=align_report.overlap_score,
                    question_subject=align_report.question_subject,
                    observation_subjects=align_report.observation_subjects,
                    falsifiable_reason=align_report.falsifiable_reason,
                )
        except InsufficientObservationsError as exc:
            console.print(
                f"\n[red]❌ 入口对齐校验失败 "
                f"(overlap={exc.overlap_score}/5)[/red]\n"
            )
            console.print(f"Question 主体: {exc.question_subject}")
            console.print("Observation 主体:")
            for s in exc.observation_subjects:
                console.print(f"  - {s}")
            console.print(f"\n理由: {exc.falsifiable_reason}")
            console.print(
                "\n[dim]💡 若认为是误杀, 用 --no-input-check 重试[/dim]"
            )
            raise typer.Exit(2) from exc
        except (LLMError, SchemaValidationError) as exc:
            console.print(f"[red]input validation LLM 失败: {exc}[/red]")
            raise typer.Exit(1) from exc
    elif session.state.tick > 0:
        console.print(
            f"[dim](resuming at tick={session.state.tick}, "
            f"skipping input validation)[/dim]"
        )

    def on_tick(_state: CognitiveState) -> None:
        store.save(session)

    try:
        reason = asyncio.run(runtime_run(session.state, llm, budget=budget, on_tick=on_tick))
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]runtime failed: {exc}[/red]")
        raise typer.Exit(1) from exc

    session.meta.stage = "converged"
    store.save(session)

    drivers = [nid for nid, n in session.state.graph.nodes.items() if n.abstraction_level == 2]
    console.print(
        f"\n[green]Phase 5 run complete[/green] "
        f"(reason={reason}, tick={session.state.tick})"
    )
    console.print(
        f"graph: {len(session.state.graph.nodes)} nodes / "
        f"{len(session.state.graph.edges)} edges"
    )
    console.print(f"driver layer: {len(drivers)} drivers added")


@app.command()
def check(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    target_id: str | None = typer.Argument(
        None,
        help="单个 variable id (c_NNN / d_NNN). 不传则 batch check 全 L1+L2.",
    ),
    trace_all: bool = typer.Option(
        False, "--trace-all", help="渲染完整 decay_trace (默认 top 8)"
    ),
) -> None:
    """Phase 6 consistency check: 数学验证 abstract/driver 能否 rollout 出 concrete.

    Pure rule-based, 0 LLM call. 适合在已 converged session 上 audit graph 质量.

    Examples:
        explain check s_f3beb777              # batch: 全 graph L1+L2
        explain check s_f3beb777 c_001        # 单 target: 只 check c_001
    """
    from explain_engine.engines.simulation import (
        check_consistency,
        check_consistency_batch,
    )

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        if target_id is None:
            reports = check_consistency_batch(session.state)
            if not reports:
                console.print("[yellow]graph 无 L1/L2 节点可 check[/yellow]")
                # 仍然显示 multi-signal (尤其 only-L0 graph 的 rollout_coverage)
                _render_multi_signal(session.state)
                return
            _render_batch_reports(reports, session)
        else:
            report = check_consistency(session.state, target_id)
            _render_single_report(report, session, trace_all=trace_all)

        # Wave 2 Phase 8: multi-signal aggregate (always show after per-target view).
        _render_multi_signal(session.state)
    except ValueError as exc:
        console.print(f"[red]invalid target: {exc}[/red]")
        raise typer.Exit(2) from exc


def _color_for(score: float) -> str:
    if score >= 0.7:
        return "green"
    if score >= 0.4:
        return "yellow"
    return "red"


def _render_parsed(parsed, state) -> None:
    """渲染 parser 输出 (Wave B fix2 / Fix A): 透明化 existing_refs + new_concepts.

    格式:
      解析:
        existing_refs: c_001(name), d_002(name), ...
        new_concepts: [{name, level=2}, ...]
    """
    console.print("\n  解析:")
    if parsed.existing_refs:
        refs_str = ", ".join(
            f"{rid}({state.graph.nodes[rid].name})"
            if rid in state.graph.nodes else rid
            for rid in parsed.existing_refs
        )
        console.print(f"    existing_refs: {refs_str}")
    else:
        console.print("    existing_refs: (none)")
    if parsed.new_concepts:
        concepts_str = ", ".join(
            f"{{name={c.name}, level={c.expected_level}}}"
            for c in parsed.new_concepts
        )
        console.print(f"    new_concepts: {concepts_str}")
    else:
        console.print("    new_concepts: (none)")


def _render_batch_reports(reports, session) -> None:
    g = session.state.graph
    n_L0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_L1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_L2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    console.print(
        f"\n[bold]Consistency Check: {session.meta.session_id}[/bold]"
    )
    console.print(
        f"  (graph: {n_L0} L0 + {n_L1} L1 + {n_L2} L2 = {len(g.nodes)} nodes)\n"
    )

    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("Lvl", justify="right")
    table.add_column("Consistency", justify="right")
    table.add_column("Essentialness", justify="right")
    for r in reports:
        node = g.nodes[r.target_id]
        c_color = _color_for(r.consistency_score)
        e_color = _color_for(r.essentialness_score)
        table.add_row(
            r.target_id,
            node.name,
            str(node.abstraction_level),
            f"[{c_color}]{r.consistency_score:.2f}[/{c_color}]",
            f"[{e_color}]{r.essentialness_score:.2f}[/{e_color}]",
        )
    console.print(table)

    if reports:
        lowest_c = min(reports, key=lambda r: r.consistency_score)
        lowest_e = min(reports, key=lambda r: r.essentialness_score)
        all_weak: set[str] = set()
        for r in reports:
            all_weak.update(r.weak_chains)
        console.print(
            f"\nLowest consistency: {lowest_c.target_id} ({lowest_c.consistency_score:.2f})"
        )
        console.print(
            f"Lowest essentialness: {lowest_e.target_id} ({lowest_e.essentialness_score:.2f})"
        )
        if all_weak:
            console.print(f"Weak chains in any: {', '.join(sorted(all_weak))}")


def _render_multi_signal(state) -> None:
    """Wave 2 Phase 8: 渲染 AcceptanceReport multi-signal section.

    优先读 state.last_acceptance_report (Wave 3 input_alignment / falsifiable_reason
    存活), fallback 当场聚合 (CLI from-disk load 时 cache 必为 None).
    """
    report = getattr(state, "last_acceptance_report", None)
    if report is None:
        from explain_engine.engines.simulation import aggregate_acceptance
        try:
            report = aggregate_acceptance(state)
        except Exception as exc:
            console.print(f"\n[dim](无法计算 multi-signal: {exc})[/dim]")
            return

    console.print(
        "\n[bold cyan]═══ Multi-signal acceptance (Phase 8 Wave 2) ═══[/bold cyan]"
    )
    console.print(f"  avg_consistency       {report.avg_consistency:.3f}")
    console.print(f"  avg_essentialness     {report.avg_essentialness:.3f}")
    console.print(f"  weak_chain_l1s        {report.weak_chain_l1s}")
    if report.lowest_l1:
        l1, score = report.lowest_l1
        console.print(f"  lowest_l1             {l1} (consistency={score:.3f})")
    else:
        console.print("  lowest_l1             (none)")
    console.print(f"  consistency_spread    {report.consistency_spread:.3f}")
    console.print(f"  essentialness_spread  {report.essentialness_spread:.3f}")
    console.print(
        f"  rollout_coverage      {report.rollout_coverage:.3f} "
        f"({len(report.missing_l0)} missing L0)"
    )
    if report.missing_l0:
        console.print(f"  missing_l0            {report.missing_l0}")

    # Wave 3 Phase 8: Falsifiability section — always shown to make 3 None cases visible:
    # (a) 旧 session 没跑过 input_validation
    # (b) --no-input-check 跳过
    # (c) tick > 0 resume 跳过
    console.print(
        "\n[bold cyan]═══ Falsifiability (Phase 8 Wave 3) ═══[/bold cyan]"
    )
    if report.input_alignment is not None:
        score = round(report.input_alignment * 5)
        console.print(f"  input_alignment       {score}/5")
        console.print(f"  falsifiable_reason    {report.falsifiable_reason}")
    else:
        console.print(
            "  input_alignment       [dim](not checked; "
            "use `explain run` at tick 0 without --no-input-check)[/dim]"
        )
    console.print(
        f"  rollout_alignment     {report.rollout_coverage:.3f}  "
        f"(= rollout_coverage; same算法, Q6.2 共用)"
    )


def _render_single_report(report, session, trace_all: bool = False) -> None:
    g = session.state.graph
    node = g.nodes[report.target_id]
    console.print(
        f"\n[bold]ConsistencyReport: {session.meta.session_id} → "
        f"{report.target_id} {node.name} (L{node.abstraction_level})[/bold]\n"
    )

    c_color = _color_for(report.consistency_score)
    e_color = _color_for(report.essentialness_score)
    console.print(
        f"  consistency_score:    "
        f"[{c_color}]{report.consistency_score:.2f}[/{c_color}]   "
        f"(mean activation over reachable L0)"
    )
    console.print(
        f"  essentialness_score:  "
        f"[{e_color}]{report.essentialness_score:.2f}[/{e_color}]   "
        f"(Σ contribution / |L0|)"
    )
    console.print(
        f"  reachable L0:         {len(report.reachable_L0)}   {report.reachable_L0}"
    )
    if report.weak_chains:
        console.print(
            f"  weak chains (<{WEAK_CHAIN_THRESHOLD:.2f}):  {len(report.weak_chains)}   {report.weak_chains}"
        )

    if report.contribution_breakdown:
        console.print("\n[bold]Contribution Breakdown[/bold] (baseline - without_target):")
        ct = Table()
        ct.add_column("L0 ID", style="cyan")
        ct.add_column("名称", style="dim")
        ct.add_column("Contribution", justify="right")
        for lid, contrib in sorted(
            report.contribution_breakdown.items(),
            key=lambda kv: -kv[1],
        ):
            node_name = g.nodes[lid].name if lid in g.nodes else lid
            ct.add_row(lid, node_name, f"{contrib:.2f}")
        console.print(ct)

    if report.decay_trace:
        steps = report.decay_trace
        if not trace_all:
            steps = sorted(steps, key=lambda s: -s.activation_after)[:8]
            console.print(
                "\n[bold]Decay Trace[/bold] (top 8 by activation_after, --trace-all 看完整):"
            )
        else:
            console.print("\n[bold]Decay Trace[/bold] (full):")
        tt = Table()
        tt.add_column("depth", justify="right")
        tt.add_column("src")
        tt.add_column("→")
        tt.add_column("dst")
        tt.add_column("edge")
        tt.add_column("conf", justify="right")
        tt.add_column("act_before", justify="right")
        tt.add_column("→")
        tt.add_column("act_after", justify="right")
        for s in steps:
            tt.add_row(
                str(s.depth),
                s.src,
                "→",
                s.dst,
                s.edge_id,
                f"{s.edge_confidence:.2f}",
                f"{s.activation_before:.2f}",
                "→",
                f"{s.activation_after:.2f}",
            )
        console.print(tt)


@app.command()
def predict(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    intervention_text: str = typer.Argument(..., help="intervention 描述 (自然语言)"),
) -> None:
    """Phase 7 Wave B: forward prediction.

    parser → 加 new_concepts → LLM 生 predicted L0 → propagate → HITL.

    Examples:
        explain predict s_f3beb777 "现代媒体放大效应"
    """
    asyncio.run(_run_predict(session_id, intervention_text))


async def _run_predict(session_id: str, intervention_text: str) -> None:
    from explain_engine.engines import prediction as prediction_mod
    from explain_engine.hitl.cli_interactive import review_predicted_l0

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    # Phase 7 design §5.7: predict 必须 stage ∈ {done, converged}
    if session.meta.stage not in ("done", "converged"):
        console.print(
            f"[red]predict 要求 stage ∈ {{done, converged}}, "
            f"实际 stage={session.meta.stage!r}. "
            f"先跑 explain compress (→ done) 或 explain run (→ converged).[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()
    try:
        report = await prediction_mod.predict(session.state, intervention_text, llm)
    except ValueError as exc:
        console.print(f"[red]parser 失败: {exc}[/red]")
        raise typer.Exit(2) from exc
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Forward prediction: {intervention_text}[/bold]")
    _render_parsed(report.parsed, session.state)
    if report.new_node_ids:
        console.print(f"  新增 intervention nodes: {report.new_node_ids}")
    if report.predicted_L0_ids:
        console.print("\n[bold]Predicted new L0 (待审):[/bold]")
        kept = review_predicted_l0(
            session.state, report.predicted_L0_ids, console=console,
        )
        console.print(
            f"\n保留 {len(kept)}/{len(report.predicted_L0_ids)} predicted L0"
        )
    if report.activated_existing_L0:
        console.print(
            f"\n[bold]Existing L0 也被激活:[/bold] {report.activated_existing_L0}"
        )
    if (
        not report.new_node_ids
        and not report.predicted_L0_ids
        and not report.activated_existing_L0
    ):
        console.print(
            "\n[yellow]⚠ 无明显 effect: parser 识别的 intervention 下游 propagation "
            "到达节点都被 filter (speculation epistemic 不算激活 existing L0). 建议:\n"
            "  - 用更具体的 driver/abstract 名称作 intervention\n"
            "  - 引入新机制变量 (description 强调 level=2 driver)[/yellow]"
        )
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]session 保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc


@app.command()
def counterfactual(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    intervention_text: str = typer.Argument(..., help="intervention 描述 (自然语言)"),
) -> None:
    """Phase 7 Wave B: counterfactual remove + (optional) substitute.

    副作用 = 0: 不改 state.graph. 深拷贝跑 propagate, 拷贝丢弃.

    Examples:
        explain counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"
        explain counterfactual s_f3beb777 "如果删除集体身份维系压力"
    """
    asyncio.run(_run_counterfactual(session_id, intervention_text))


async def _run_counterfactual(session_id: str, intervention_text: str) -> None:
    from explain_engine.engines import counterfactual as counterfactual_mod

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    # Phase 7 design §5.7: counterfactual 要求 stage ∈ {done, converged}
    if session.meta.stage not in ("done", "converged"):
        console.print(
            f"[red]counterfactual 要求 stage ∈ {{done, converged}}, "
            f"实际 stage={session.meta.stage!r}. "
            f"先跑 explain compress (→ done) 或 explain run (→ converged).[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()
    try:
        report = await counterfactual_mod.substitute(
            session.state, intervention_text, llm,
        )
    except ValueError as exc:
        console.print(f"[red]parser 失败: {exc}[/red]")
        raise typer.Exit(2) from exc
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Counterfactual: {intervention_text}[/bold]")
    _render_parsed(report.parsed, session.state)
    console.print(f"  Removed: {report.removed_node_ids}")
    if report.added_node_ids:
        console.print(f"  Substituted with: {report.added_node_ids}")
    if report.alt_narrative:
        console.print(f"\n[bold]Alt narrative:[/bold]\n{report.alt_narrative}")
    # 渲染 diff table (top changes, |delta| > 0.05)
    significant = sorted(
        [(nid, v) for nid, v in report.activation_diff.items() if abs(v) > 0.05],
        key=lambda kv: -abs(kv[1]),
    )[:10]
    if significant:
        console.print("\n[bold]Top activation diff (baseline - counterfactual):[/bold]")
        for nid, v in significant:
            console.print(f"  {nid}: {v:+.2f}")
    if (
        not report.removed_node_ids
        and not report.added_node_ids
        and not significant
    ):
        console.print(
            "\n[yellow]⚠ 无明显 effect: parser 未识别可 remove/substitute 的节点, "
            "或 activation diff 全 < 0.05.[/yellow]"
        )
    # 副作用 = 0, 不需要 store.save


@app.command()
def rescore(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """Phase 7 Wave D: 重评 existing session 的 edge.confidence.

    Wave A acceptance fixture. 用 scoring.yaml prompt 评分 manifests_as + causes edges,
    写回 edge.confidence = score / 5.0 (跟 Wave A.1/A.2 同 mapping).
    并发跑 (LLM_MAX_CONCURRENCY=5).

    LLM cost: ~17 manifests_as + ~8 causes ≈ 25 LLM call per session.

    Examples:
        explain rescore s_f3beb777
    """
    asyncio.run(_run_rescore(session_id))


async def _run_rescore(session_id: str) -> None:
    from explain_engine.engines import rescore as rescore_mod

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    llm = make_llm_client()
    try:
        new_confs = await rescore_mod.rescore_session(session.state, llm)
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]rescore 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]save 失败 (LLM cost 已消耗): {exc}[/red]")
        raise typer.Exit(3) from exc

    if not new_confs:
        console.print(
            f"[yellow]session {session_id}: 无 manifests_as/causes edges 可 rescore[/yellow]"
        )
        return

    avg = sum(new_confs.values()) / len(new_confs)
    console.print(
        f"\n[green]Rescored {len(new_confs)} edges in {session_id}[/green]"
    )
    console.print(f"  Average new confidence: {avg:.2f}")


@app.command(name="list")
def list_cmd() -> None:
    """列出所有 session（按创建时间降序）。"""
    store = _get_store()
    metas = store.list()

    table = Table(title="Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)
    console.print(table)


@app.command(name="delete")
def delete_cmd(
    session_id: str = typer.Argument(..., help="要删的 session id (s_xxxxxxxx)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="跳过二次确认直接删",
    ),
) -> None:
    """删除某个 session (含 graph / transcript / chat_state / repl_history).

    Phase 17.2 Feature C: 默认 y/N 二次确认; --force 跳确认.
    不存在的 sid 红字报错 exit 1.
    """
    from explain_engine.chat.chat_copy import (
        STATUS_DELETE_CONFIRM,
        err_delete_not_found,
        msg_delete_done,
    )

    store = _get_store()
    if not force:
        # Phase 17.2 Wave 3 review fix (I-1): template 不再含 `[y/N]:`, 让
        # typer.confirm 自动加, 避免双 [y/N] 显示.
        prompt = STATUS_DELETE_CONFIRM.format(sid=session_id)
        if not typer.confirm(prompt, default=False):
            console.print("[yellow]已取消[/yellow]")
            raise typer.Exit(code=0)
    try:
        store.delete(session_id)
    except FileNotFoundError:
        console.print(f"[red]{err_delete_not_found(session_id)}[/red]")
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        # Phase 17.2 Wave 3 review fix (C-1): StorageV2 拒绝非法 sid 格式 →
        # 友好红字 + exit 1, 不暴 raw stack trace.
        console.print(f"[red]非法 session id: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"[red]删除失败 (IO/权限): {exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]{msg_delete_done(session_id)}[/green]")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 9 Wave F.2: chat REPL + migrate
# ─────────────────────────────────────────────────────────────────────────────


def _render_event(con: Console, event) -> None:
    """Phase 9 Wave F.2: 渲染单个 ChatEvent 到终端 (Rich).

    Event 类型映射:
    - assistant_text       — plain print (escape 防 Rich markup 注入)
    - tool_use             — "→ tool: <name>(<input>)" 灰色
    - tool_result          — "← <name>: <result>" 灰色, > 500 chars truncate
    - turn_complete        — 空行 separator
    - budget_exhausted     — yellow warn, 含 scope
    - slash_* (help/show/budget/save/compact/quit/error/unknown) — 直接打印 content
    - 其他                  — fallback "<type>: <content>" 灰色
    """
    from rich.markup import escape

    t = event.type

    if t == "assistant_text":
        con.print(escape(str(event.content or "")))
    elif t == "tool_use":
        name = getattr(event, "tool_name", "?")
        inp = getattr(event, "tool_input", {})
        con.print(f"[dim]→ tool: {escape(str(name))}({escape(str(inp))})[/dim]")
    elif t == "tool_result":
        # ToolResultEvent 用 .result 字段 (非 .content)
        result_str = getattr(event, "result", None) or (event.content or "")
        result_str = str(result_str)
        if len(result_str) > 500:
            result_str = result_str[:500] + "..."
        name = getattr(event, "tool_name", "?")
        con.print(f"[dim]← {escape(str(name))}: {escape(result_str)}[/dim]")
    elif t == "turn_complete":
        # Wave F.2 hotfix: surface silent fallback marker.
        content_str = str(event.content or "")
        if content_str == "llm_client_lacks_tools_api":
            con.print(
                "[yellow](no response: LLM dispatch 未实装. "
                "见启动时警告. 试 /show 或其他 slash 命令.)[/yellow]"
            )
        else:
            con.print()
    elif t == "budget_exhausted":
        scope = getattr(event, "scope", "unknown")
        scope_zh_map = {"per_turn": "本轮", "per_session": "本 session"}
        scope_zh = scope_zh_map.get(scope, scope)
        con.print(
            f"[yellow]预算耗尽 ({scope_zh}). "
            f"用 /budget 重设或开新一轮对话.[/yellow]"
        )
    elif t == "slash_next_step_hint":
        # Phase 14: 灰色 dim 提示 (with_stage_gate 装饰器 yield).
        con.print(f"[dim]{escape(str(event.content or ''))}[/dim]")
    elif t.startswith("slash_"):
        con.print(escape(str(event.content or "")))
    else:
        # Generic fallback (placeholder / unknown future events)
        con.print(f"[dim]{escape(t)}: {escape(str(event.content or ''))}[/dim]")


@app.command()
def chat(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    no_input_check: bool = typer.Option(
        False, "--no-input-check",
        help="(Phase 9 Wave G+ TODO: wire input_validation into chat startup)",
        hidden=True,  # 隐藏直到 Wave G+ 真接通 input_validation 到 chat 路径
    ),
) -> None:
    """Phase 9 Wave F.2: 进 conversational chat REPL.

    交互模式 — stdin 读 user input, dispatch 到 ChatSession.handle_user_input.
    Slash command 本地 intercept, 其余走 LLM ↔ tools while-loop.
    /new + /resume (2026-05-18) 可触发 in-process session 热切.

    Phase 11 Wave 2.5: 删除 tool budget cli flag — 改通过 chat 内 /budget slash
    interactive 配置.

    优雅退出:
    - /quit            → slash_quit event 触发 break
    - Ctrl-D / Ctrl-C  → EOFError / KeyboardInterrupt 捕获 break
    """
    del no_input_check  # silence ARG001
    llm = make_llm_client()
    asyncio.run(_run_chat_repl_async(
        initial_sid=session_id,
        llm=llm,
    ))


async def _run_chat_repl_async(
    initial_sid: str,
    llm: "LLMClient | None",
) -> None:
    """REPL 主循环 (Wave 2 抽出 + 2026-05-18 prompt_toolkit 升级).

    chat 模式期间:
    - swap root logging handler (stdout → BufferedLogHandler), 退出 restore
    - 用 prompt_toolkit PromptSession + read_input 替代 asyncio.to_thread(input)
    - 用户输入 ctrl+o 弹 log popup 看 buffered log (灰色样式)
    - 输 / 自动弹 slash command 联想菜单

    切换契约: handler yield ChatEvent(type='slash_switch_session', ...) 后,
    本函数 single turn iter 结束后做 aclose+reload (Wave 2 不变).

    Phase 11 Wave 2.5: budget 配置改 /budget slash, 不再由 cli flag 注入
    chat_state — limit 由 ChatStateDict default (10/50) 起步.
    """
    from explain_engine.chat.repl_input import (
        BufferedLogHandler,
        make_session,
        read_input,
    )
    from explain_engine.chat.session import ChatSession, ChatSessionLoadError
    from explain_engine.engines.lexicon import init_lexicon_backend

    # Phase 17.1 Wave 9: init lexicon backend before chat starts
    use_pg = await init_lexicon_backend()
    backend_label = "PostgreSQL (远程)" if use_pg else "本地 JSON (PG 不可达)"
    console.print(f"[dim]Lexicon backend: {backend_label}[/dim]")

    # ── chat 模式 enter: swap logging handler ──
    log_handler = BufferedLogHandler(capacity=200)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers = [log_handler]
    root_logger.setLevel(logging.INFO)

    try:
        try:
            chat_session = ChatSession(initial_sid, llm=llm)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        has_tools_api = (
            hasattr(llm, "chat_with_tools") if llm is not None else False
        )
        console.print(
            f"[dim]Loaded session {initial_sid}. "
            f"Type /help for commands. /quit to exit. ctrl+o toggle log.[/dim]"
        )
        if llm is not None and not has_tools_api:
            console.print(
                "[yellow]⚠️  LLM dispatch 未实装 (LLMClient.chat_with_tools 不存在). "
                "自然语言输入会无响应; 仅 slash 命令工作.[/yellow]"
            )

        # Phase 16.2 Wave 7 Task 7.10 (+ 16.2.1 hotfix): resume banner 追加 history 段.
        # 抽 helper print_history_section 让 repl_entry.py /resume 切 session 路径也复用.
        from explain_engine.chat.history_render import print_history_section
        print_history_section(console, chat_session)

        # ── Build prompt_toolkit session (reuse across turns for history) ──
        pt_session = make_session(log_handler)

        # F-1 (2026-05-18 Wave 5 review follow-up): 暴露 prompt_toolkit input
        # 给 slash handler 用 (e.g. /resume picker 收选号). 让 sub-prompt 也走
        # prompt_toolkit, 享 bottom toolbar + ctrl+o + 中文 backspace fix,
        # 而非 bare input() 回 readline regression.
        chat_session.input_provider = lambda prompt: read_input(
            pt_session, prompt_text=prompt
        )

        while True:
            try:
                user_input = await read_input(pt_session)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Interrupted. Saving...[/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            quit_requested = False
            switch_to_sid: str | None = None
            reset_to_ephemeral = False
            try:
                async for event in chat_session.handle_user_input(
                    user_input, llm=llm
                ):
                    # I-1 (review 2026-05-18): slash_switch_session 是内部 signal,
                    # 不渲染 dict payload (UX noise: `{'sid': 's_xxx'}` 字面 dump).
                    # 实际 "Switched to X" 由切换分支显式 print.
                    if event.type == "slash_switch_session":
                        switch_to_sid = event.content["sid"]
                        continue
                    if event.type == "slash_reset_to_ephemeral":
                        # /new 在 explain chat <sid> 这条 session-bound REPL 里
                        # 无法直接转 ephemeral (chat_session 类型固定 ChatSession).
                        # 退出 REPL + 提示用户跑 `explain` 重启进 ephemeral.
                        reset_to_ephemeral = True
                        continue
                    _render_event(console, event)
                    if event.type == "slash_quit":
                        quit_requested = True
            except Exception as exc:
                console.print(f"[red]Error: {type(exc).__name__}: {exc}[/red]")
                continue

            if reset_to_ephemeral:
                console.print(
                    "[yellow]/new: 当前 chat 已保存. "
                    "在 `explain chat <sid>` 路径中无法直接转 ephemeral; "
                    "输 `explain` 重启进 ephemeral REPL.[/yellow]"
                )
                break

            # 单 turn 结束后再切, 避免 iter 中 mutate
            if switch_to_sid and switch_to_sid != chat_session.sid:
                # I-2 (review 2026-05-18): snapshot 切前 sid, fallback 回上次成功的,
                # 不回 initial. 防多次切换后 surprise (用户期望: 失败 → 回当前 sid).
                old_sid = chat_session.sid
                await chat_session.aclose()
                try:
                    chat_session = ChatSession(switch_to_sid, llm=llm)
                except (FileNotFoundError, ChatSessionLoadError) as exc:
                    console.print(f"[red]切换失败: {exc}[/red]")
                    try:
                        chat_session = ChatSession(old_sid, llm=llm)
                    except (FileNotFoundError, ChatSessionLoadError) as recover_exc:
                        console.print(
                            f"[red]恢复原 session ({old_sid}) 也失败: "
                            f"{recover_exc}. 退出.[/red]"
                        )
                        return
                # Phase 11 Wave 2.5: 不再注入 cli flag 到 chat_state — budget
                # 是 per-session 配置 (新 session 用自己 chat_state.json 的值,
                # 或 /budget 重新调).
                # F-1: 切 session 后新 ChatSession 实例的 input_provider 默认 None,
                # 需重新挂 lambda 让下次 /resume picker 仍走 prompt_toolkit.
                chat_session.input_provider = lambda prompt: read_input(
                    pt_session, prompt_text=prompt
                )
                console.print(
                    f"[green]Switched to {chat_session.sid}.[/green]"
                )

            if quit_requested:
                break

        await chat_session.aclose()
        console.print(f"[green]Session {chat_session.sid} saved.[/green]")

        # Phase 10 Wave 4: chat 退出时 flush lexicon (chat 期间可能产新 var,
        # 包括 graph 增 L1/L2). best-effort: exception 不 fail chat exit.
        # aclose 已 persist session, 这里仅同步到 cross-session lexicon.
        try:
            from explain_engine.engines.lexicon import flush_to_lexicon
            from explain_engine.persistence.storage_v2 import StorageV2
            n = await flush_to_lexicon(
                chat_session._session, StorageV2(), llm=llm,
            )
            if n > 0:
                console.print(f"[dim]{n} var 写入 lexicon[/dim]")
        except Exception as exc:
            console.print(
                f"[yellow]lexicon flush 失败 (非关键): {type(exc).__name__}: {exc}[/yellow]"
            )
    finally:
        # ── chat 模式 exit: restore log handlers ──
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)


@app.command()
def migrate(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Preview migration without moving files",
    ),
) -> None:
    """Phase 9 Wave F.2: 一次性 sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/.

    检测当前目录 sessions/ 下 legacy session files, 拆 {meta, state} →
    metadata.json + graph.json, 移 legacy 到 sessions/.legacy/ (安全:
    永不删用户数据). Idempotent — 重跑只处理新 legacy.

    用 --dry-run 预览, 不移文件.
    """
    from explain_engine.persistence.migration import (
        detect_legacy_sessions,
        migrate_all,
    )

    sids = detect_legacy_sessions()

    if not sids:
        console.print("[green]No legacy sessions to migrate.[/green]")
        return

    console.print(f"Found {len(sids)} legacy session(s): {sids}")
    if dry_run:
        console.print("[yellow]--dry-run: no files will be moved.[/yellow]")

    results = migrate_all(dry_run=dry_run)
    for r in results:
        sid = r["sid"]
        if r["migrated"]:
            console.print(f"  ✓ {sid} migrated")
        else:
            console.print(f"  ⊘ {sid}: {r['reason']}")

    migrated_count = sum(1 for r in results if r["migrated"])
    if dry_run:
        console.print(
            f"\n[dim]Dry-run: {len(sids)} session(s) would be migrated.[/dim]"
        )
    else:
        console.print(
            f"\n[green]Migrated {migrated_count}/{len(sids)} sessions.[/green]"
        )


@app.command()
def lexicon(
    dump_json: bool = typer.Option(
        False, "--dump-json", help="raw JSON 输到 stdout"
    ),
    top_k: int = typer.Option(
        0, "--top-k", help="仅显 top-K (默认 0=全显)"
    ),
) -> None:
    """显 knowledge/variables.json 内容 (Phase 10 lexicon)."""
    import json as _json

    from explain_engine.engines.lexicon import (
        _load_lexicon,
        _select_top_k_vars,
    )
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    path = storage.knowledge_dir() / "variables.json"
    lex = _load_lexicon(path)

    if dump_json:
        print(_json.dumps(lex, indent=2, ensure_ascii=False))
        return

    variables = lex["variables"]
    if top_k > 0:
        variables = _select_top_k_vars(lex, k=top_k)

    if not variables:
        console.print(
            "[dim]lexicon 暂无变量. "
            "跑 explain compress / chat 完成后再看.[/dim]"
        )
        return

    table = Table(title=f"Variable Lexicon ({len(variables)} vars)")
    table.add_column("global_id", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("Level", justify="right")
    table.add_column("reuse", justify="right")
    table.add_column("avg_ess", justify="right")
    table.add_column("last_seen", style="dim")
    for v in variables:
        table.add_row(
            v["global_id"],
            v["name"],
            f"L{v['abstraction_level']}",
            str(v["fitness"]["reuse_count"]),
            f"{v['fitness']['avg_essentialness']:.2f}",
            v["fitness"]["last_seen_at"][:10],
        )
    console.print(table)


@app.command()
def theories(
    project: str | None = typer.Option(None, "--project", help="项目 ID, 默 cwd"),
    show_all: bool = typer.Option(False, "--all", help="显 stable + tentative"),
    limit: int = typer.Option(10, "--limit", min=1, max=50),
    force_recompute: bool = typer.Option(
        False, "--force", help="强制重算 cache"
    ),
) -> None:
    """显跨 session 因果模式 (Phase 16 Theory Formation, cross-session inspect)."""
    from explain_engine.engines.theory.cache import get_active_theories
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2(project_id=project) if project else StorageV2()

    # Try BGE-M3 singleton, fallback None (test env disable / load fail)
    embedder: object | None
    try:
        from explain_engine.embedding.bge_m3 import get_embedder
        embedder = get_embedder()
    except Exception:
        embedder = None

    with console.status("[bold green]正在分析跨 session 模式..."):
        cache = get_active_theories(
            storage, embedder=embedder, force_recompute=force_recompute,
        )

    n_sessions = len(cache.session_ids_snapshot)
    if n_sessions < cache.cold_start_threshold:
        console.print(
            f"需累积 ≥ {cache.cold_start_threshold} 个 session 才能形成 theory. "
            f"当前: {n_sessions}/{cache.cold_start_threshold}."
        )
        return

    all_theories = list(cache.stable_theories)
    if show_all:
        all_theories.extend(cache.tentative_theories)
    visible = [
        t for t in all_theories if t.id not in cache.rejected_theory_ids
    ][:limit]

    if not visible:
        console.print(
            f"已分析 {n_sessions} 个 session, 未发现重复出现的因果模式."
        )
        return

    type_zh_map = {"chain": "因果链", "star": "星型", "cycle": "环路"}
    table = Table(title=(
        f"跨 session 因果模式 (stable: {len(cache.stable_theories)}, "
        f"tentative: {len(cache.tentative_theories)}, "
        f"rejected: {len(cache.rejected_theory_ids)})"
    ))
    table.add_column("ID", style="cyan")
    table.add_column("类型")
    table.add_column("模式", style="bold", max_width=50)
    table.add_column("覆盖 session", justify="right")
    table.add_column("预测准确度", justify="right")
    table.add_column("状态")

    for t in visible:
        type_zh = type_zh_map.get(t.motif_type, t.motif_type)
        status_zh = "已稳定" if t.stability_status == "stable" else "待观察"
        rejected_mark = " (已拒绝)" if t.id in cache.rejected_theory_ids else ""
        table.add_row(
            t.id, type_zh, t.natural_language_summary,
            f"{len(t.supporting_sessions)}/{n_sessions}",
            f"{t.predictive_power:.0%}",
            status_zh + rejected_mark,
        )
    console.print(table)


@app.command("migrate-lexicon-pg")
def migrate_lexicon_pg_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="仅预览, 不写 DB / 不 rename json"
    ),
) -> None:
    """Phase 17.1 Wave 7: 一次性把 knowledge/variables.json 迁到 PostgreSQL.

    Pre-req:
      - EXPLAIN_DB_URL 已设 (e.g. postgresql://explain:<密码>@host:5432/explain).
      - PG server 运行 (远程: ssh host 'docker compose ps').
      - schema 已建 (init/01-init.sql 自动跑 in fresh container).

    成功 (insert > 0): variables.json → variables.json.migrated (backup).
    失败 (transaction rollback): json 仍在原位, 可 retry.
    Idempotent: ON CONFLICT (global_id) DO NOTHING — 重跑只插新.

    --dry-run: 仅 count would_migrate, 不动 DB 不动 json.
    """
    from explain_engine.persistence.lexicon_migrations import (
        migrate_json_to_pg,
    )
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    result = asyncio.run(migrate_json_to_pg(storage, dry_run=dry_run))
    console.print(f"[green]Migration:[/green] {result}")


if __name__ == "__main__":
    app()
