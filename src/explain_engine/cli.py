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
import readline  # noqa: F401 — imported for side effect (enable line editing)
from datetime import datetime
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatSession
    from explain_engine.llm.client import LLMClient
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings, make_llm_client
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
    no_args_is_help=True,
)
console = Console()


def _get_store() -> SessionStore:
    # Phase 9: storage_v2 reads EXPLAIN_HOME / EXPLAIN_PROJECT_ID env vars directly
    return SessionStore()


@app.command()
def new(
    question: str = typer.Argument(..., help="为什么 X 问题"),
) -> None:
    """启动新 session：Bootstrap + HITL 1 + 落 session。"""
    asyncio.run(_run_new(question))


async def _run_new(question: str) -> None:
    settings = Settings()
    llm = make_llm_client()

    import os
    proto = os.environ.get("LLM_PROTOCOL", "?")
    model = os.environ.get("LLM_MODEL", "?")
    console.print(f"\n[INFO] 调 LLM ({proto} / {model}) 生现象...")
    try:
        phenomena = await bootstrap_phenomena(question, llm)
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
    console.print(f"       下一步：explain show {meta.session_id}")


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
            await propose_candidates(session.state, llm)
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
        con.print(
            f"[yellow]Budget exhausted ({scope}). "
            f"Reset via /budget or new turn.[/yellow]"
        )
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
    tool_budget_per_turn: int = typer.Option(
        10, "--tool-budget-per-turn",
        help="Max tool calls per user turn (Phase 9 Q5γ)",
    ),
    tool_budget_per_session: int = typer.Option(
        50, "--tool-budget-per-session",
        help="Max tool calls per session lifetime",
    ),
) -> None:
    """Phase 9 Wave F.2: 进 conversational chat REPL.

    交互模式 — stdin 读 user input, dispatch 到 ChatSession.handle_user_input.
    Slash command 本地 intercept, 其余走 LLM ↔ tools while-loop.
    /new + /resume (2026-05-18) 可触发 in-process session 热切.

    优雅退出:
    - /quit            → slash_quit event 触发 break
    - Ctrl-D / Ctrl-C  → EOFError / KeyboardInterrupt 捕获 break
    """
    del no_input_check  # silence ARG001
    llm = make_llm_client()
    asyncio.run(_run_chat_repl_async(
        initial_sid=session_id,
        llm=llm,
        tool_budget_per_turn=tool_budget_per_turn,
        tool_budget_per_session=tool_budget_per_session,
    ))


def _apply_budget_flags(
    chat_session: "ChatSession",
    tool_budget_per_turn: int,
    tool_budget_per_session: int,
) -> None:
    """启动 / 切换 session 后, 应用 cli flag 到 chat_state.

    切 session 后 budget flag 继承 (会话级偏好, 不是 per-session config).
    """
    chat_session.chat_state.budget_per_turn_limit = tool_budget_per_turn
    chat_session.chat_state.budget_per_turn_remaining = tool_budget_per_turn
    chat_session.chat_state.budget_per_session_limit = tool_budget_per_session
    if (
        chat_session.chat_state.budget_per_session_remaining
        > tool_budget_per_session
    ):
        chat_session.chat_state.budget_per_session_remaining = tool_budget_per_session


async def _run_chat_repl_async(
    initial_sid: str,
    llm: "LLMClient | None",
    tool_budget_per_turn: int,
    tool_budget_per_session: int,
) -> None:
    """REPL 主循环 (从 chat() 命令抽出便于测试 + slash 切换需 mutable chat_session ref).

    切换契约: handler yield ChatEvent(type='slash_switch_session', content={'sid': X})
    后, 本函数在单 turn iter 结束后做 `await old.aclose() + new ChatSession(X, llm)`,
    并 _apply_budget_flags 继承 cli flag.
    """
    from explain_engine.chat.session import ChatSession, ChatSessionLoadError

    try:
        chat_session = ChatSession(initial_sid, llm=llm)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _apply_budget_flags(
        chat_session, tool_budget_per_turn, tool_budget_per_session
    )

    has_tools_api = (
        hasattr(llm, "chat_with_tools") if llm is not None else False
    )
    console.print(
        f"[dim]Loaded session {initial_sid}. "
        f"Type /help for commands. /quit to exit.[/dim]"
    )
    if llm is not None and not has_tools_api:
        console.print(
            "[yellow]⚠️  LLM dispatch 未实装 (LLMClient.chat_with_tools 不存在). "
            "自然语言输入会无响应; 仅 slash 命令工作.[/yellow]"
        )

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Interrupted. Saving...[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        quit_requested = False
        switch_to_sid: str | None = None
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
                _render_event(console, event)
                if event.type == "slash_quit":
                    quit_requested = True
        except Exception as exc:
            console.print(f"[red]Error: {type(exc).__name__}: {exc}[/red]")
            continue

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
            _apply_budget_flags(
                chat_session, tool_budget_per_turn, tool_budget_per_session
            )
            console.print(
                f"[green]Switched to {chat_session.sid}.[/green]"
            )

        if quit_requested:
            break

    await chat_session.aclose()
    console.print(f"[green]Session {chat_session.sid} saved.[/green]")


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


if __name__ == "__main__":
    app()
