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
from datetime import datetime

import typer
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
    settings = Settings()
    return SessionStore(directory=settings.sessions_dir)


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


if __name__ == "__main__":
    app()
