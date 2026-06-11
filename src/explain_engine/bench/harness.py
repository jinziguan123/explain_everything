"""H1 盲评实验 harness 实现。

协议见 docs/设计预期-修正版.md §四 H1:
- A 组: 本系统完整流程产出 (图 + 叙事报告), 无人值守 (HITL 全采纳);
- B 组: 同一底层模型、一个精心设计的深度分析 prompt、单次产出;
- 盲评: 每题输出匿名化为 X.md / Y.md, 映射写入隐藏文件 .key.json;
- 判定: A 综合胜率 ≤ 50% 即失败 (打平即失败)。

成本与公平性说明: manifest.json 记录两组的耗时与图规模, 评分时不可见
(盲评只看 X/Y 两份 markdown), 结算时用于计算成本比。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.engines.compression import propose_candidates
from explain_engine.engines.evaluation import score_all
from explain_engine.llm.client import LLMClient, Message
from explain_engine.report.dossier import build_dossier, core_variables, graph_stats
from explain_engine.report.narrative import generate_report
from explain_engine.runtime.runtime import run as runtime_run
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)

# B 组深度分析 prompt。按 H1 协议要求"精心设计"——它代表用户直接用一个好 prompt
# 能拿到的最佳单次产出, 是公平的对照组, 不是稻草人。
DEEP_PROMPT = """\
你是一名顶尖的因果分析师。请对下面这个"为什么"问题做一次深度因果分析, \
写成一份面向深度内容生产者 (研究者/分析师/专栏作者) 的中文分析报告。

要求:
1. 先列出 8-12 个与问题直接相关的可观察现象, 再从现象归纳中层机制, \
最后上溯 2-4 个深层驱动变量, 说明每一层如何生成上一层;
2. 给出 3-5 条最关键的因果链 (深层驱动 → 中层机制 → 表层现象), 标注每条链的强度;
3. 指出至少一个反直觉的点: 哪个真正重要的原因是大众直觉容易忽略的;
4. 区分确定性等级: 哪些是可验证的事实, 哪些是合理推断, 哪些只是假说, 措辞要分级;
5. 说明这个解释的边界: 它解释不了什么, 在什么条件下会失效。

报告用 markdown, 结构清晰, 不要前言, 直接开始。

问题: {question}
"""

PRIOR_PROMPT = """\
对于问题「{question}」, 一个有常识的聪明人在深入研究之前, 直觉上会预期的 \
3 个最主要原因是什么? 每行输出一个原因 (短语即可), 共 3 行, 不要编号和解释。
"""


async def elicit_prior(question: str, llm: LLMClient) -> list[str]:
    """先验引出 (§五.2): 在建图前获取代理先验 top-3 预期原因。"""
    resp = await llm.chat([Message(role="user", content=PRIOR_PROMPT.format(question=question))])
    lines = [ln.strip().lstrip("-•123456789. ") for ln in resp.text.splitlines()]
    return [ln for ln in lines if ln][:3]


def counterintuitiveness(core_names: list[str], prior_causes: list[str]) -> float:
    """反直觉度 CI = |Core \\ Prior| / |Core| (§五.2 的 Phase V 词面代理).

    匹配规则: 归一化后互相包含即算命中。embedding 匹配 (阈值 0.85) 留待
    Phase G 与置信度公式一起切换 —— 词面版偏严 (易判为反直觉), 报告中如实标注。
    """
    if not core_names:
        return 0.0

    def norm(s: str) -> str:
        return "".join(s.lower().split())

    priors = [norm(p) for p in prior_causes]
    novel = 0
    for name in core_names:
        n = norm(name)
        if not any(p and (p in n or n in p) for p in priors):
            novel += 1
    return novel / len(core_names)


async def run_pipeline_headless(
    question: str,
    llm: LLMClient,
    light_llm: LLMClient | None = None,
    budget: int = 20,
    grounded: bool = True,
    search_fn=None,
) -> tuple[CognitiveState, dict]:
    """无人值守跑完整管线: bootstrap → compress → run → (可选) 接地。

    grounded=False 跳过接地管线 — H2 实验的对照组 (§四 H2), 也是
    纯离线/无网环境的逃生门。

    HITL 等价于"全采纳": 现象全保留; insight 候选全保留 (候选节点在
    propose_candidates 时已入图, review 的 keep 路径本就是 no-op,
    只需清空 insight_candidates, 与 hitl.review_insights 的收尾一致)。

    Returns:
        (收敛后的 state, 元信息 dict: stop_reason / 各阶段耗时秒)
    """
    timings: dict[str, float] = {}

    t0 = time.monotonic()
    phenomena = await bootstrap_phenomena(
        question, llm, lexicon=None, lexicon_top_k=0, light_llm=light_llm,
    )
    state = CognitiveState.bootstrap(question, budget=budget)
    for p in phenomena:
        state.graph.add_node(p)
    timings["bootstrap_s"] = time.monotonic() - t0
    logger.info("bench: bootstrap 完成 (%d 现象)", len(phenomena))

    t0 = time.monotonic()
    await propose_candidates(state, llm)
    await score_all(state, llm)
    state.insight_candidates = []  # HITL 全采纳
    timings["compress_s"] = time.monotonic() - t0
    logger.info("bench: compress 完成 (%d L1)", sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 1
    ))

    t0 = time.monotonic()
    stop_reason = await runtime_run(state, llm, budget=budget)
    timings["run_s"] = time.monotonic() - t0
    logger.info("bench: run 完成 (reason=%s, tick=%d)", stop_reason, state.tick)

    meta: dict = {"stop_reason": stop_reason, "timings": timings}
    if grounded:
        from explain_engine.engines.grounding import ground_state

        t0 = time.monotonic()
        summary = await ground_state(
            state, light_llm or llm, search_fn=search_fn,
        )
        timings["grounding_s"] = time.monotonic() - t0
        meta["grounding"] = summary.as_dict()
        logger.info(
            "bench: 接地完成 (实证 %d / 争议 %d / 未验证 %d)",
            summary.verified, summary.contested, summary.unverified,
        )
    return state, meta


async def run_baseline(question: str, llm: LLMClient) -> str:
    """B 组: 单次深度 prompt 产出。"""
    resp = await llm.chat([Message(role="user", content=DEEP_PROMPT.format(question=question))])
    return resp.text.strip()


def _blind_assignment(seed: int, idx: int, question: str) -> bool:
    """确定性盲化: True → A 组放 X.md, False → A 组放 Y.md。"""
    digest = hashlib.sha256(f"{seed}:{idx}:{question}".encode()).digest()
    return digest[0] % 2 == 0


# 依赖注入类型 (测试替换真实管线/对照/报告)
PipelineFn = Callable[[str], Awaitable[tuple[CognitiveState, dict]]]
BaselineFn = Callable[[str], Awaitable[str]]


async def run_bench(
    questions: list[str],
    out_dir: Path,
    llm: LLMClient | None = None,
    light_llm: LLMClient | None = None,
    budget: int = 20,
    seed: int = 42,
    pipeline_fn: PipelineFn | None = None,
    baseline_fn: BaselineFn | None = None,
    mode: str = "h1",
    grounded: bool = True,
) -> dict:
    """对每个问题产出 A/B 两组结果并匿名化落盘。返回 manifest dict。

    mode (§四):
      - "h1": B 组 = 单次深度 prompt (检验"系统是否优于一个好 prompt")
      - "h2": B 组 = 同管线但关闭接地 (检验"接地是否增加信任与质量"),
              A 组强制 grounded=True

    目录结构:
        out_dir/
          q01/X.md  q01/Y.md          # 盲评材料 (评委只看这两份)
          q01/.key.json               # {"X": "A"|"B", ...} 揭盲映射
          q01/.artifacts/graph.json   # A 组图谱 (含 dossier.md), 揭盲后检视用
          评分表.md                    # 评委填写
          manifest.json               # 规模/耗时统计 (不含揭盲映射)
          .answers.json               # 全部揭盲映射汇总
    """
    if mode not in ("h1", "h2"):
        raise ValueError(f"未知 mode: {mode!r} (h1 | h2)")
    if mode == "h2":
        grounded = True  # H2 的 A 组定义即"开接地"

    if pipeline_fn is None:
        if llm is None:
            raise ValueError("run_bench 需要 llm 或注入 pipeline_fn")

        async def pipeline_fn(q: str) -> tuple[CognitiveState, dict]:
            return await run_pipeline_headless(
                q, llm, light_llm=light_llm, budget=budget, grounded=grounded,
            )

    if baseline_fn is None and llm is None:
        raise ValueError("run_bench 需要 llm 或注入 baseline_fn")
    # baseline_fn=None 时, h1/h2 的默认 B 组在循环内构造 (h2 需要 per-题 prior)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "seed": seed, "budget": budget, "mode": mode,
        "grounded": grounded, "questions": [],
    }
    answers: dict[str, dict[str, str]] = {}

    for idx, question in enumerate(questions, start=1):
        qdir = out_dir / f"q{idx:02d}"
        qdir.mkdir(parents=True, exist_ok=True)
        entry: dict = {"index": idx, "question": question}
        logger.info("bench: [%d/%d] %s", idx, len(questions), question)

        try:
            prior: list[str] = []
            if llm is not None:
                prior = await elicit_prior(question, llm)
                entry["prior_causes"] = prior

            t0 = time.monotonic()
            state, pipe_meta = await pipeline_fn(question)
            core_ids = core_variables(state)
            core_names = [
                state.graph.nodes[nid].name for nid in core_ids
                if nid in state.graph.nodes
            ]
            report_a = await generate_report(state, llm, prior_causes=prior) \
                if llm is not None else build_dossier(state, prior_causes=prior)
            a_seconds = time.monotonic() - t0

            t0 = time.monotonic()
            if baseline_fn is not None:
                report_b = await baseline_fn(question)
            elif mode == "h2":
                # H2 对照: 同管线、关接地、同 prior — 唯一变量是接地
                state_b, _meta_b = await run_pipeline_headless(
                    question, llm, light_llm=light_llm,
                    budget=budget, grounded=False,
                )
                report_b = await generate_report(state_b, llm, prior_causes=prior)
            else:
                report_b = await run_baseline(question, llm)
            b_seconds = time.monotonic() - t0

            a_is_x = _blind_assignment(seed, idx, question)
            x_text, y_text = (report_a, report_b) if a_is_x else (report_b, report_a)
            (qdir / "X.md").write_text(x_text, encoding="utf-8")
            (qdir / "Y.md").write_text(y_text, encoding="utf-8")
            key = {"X": "A" if a_is_x else "B", "Y": "B" if a_is_x else "A"}
            (qdir / ".key.json").write_text(
                json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            answers[f"q{idx:02d}"] = key

            artifacts = qdir / ".artifacts"
            artifacts.mkdir(exist_ok=True)
            (artifacts / "graph.json").write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (artifacts / "dossier.md").write_text(
                build_dossier(state, prior_causes=prior), encoding="utf-8",
            )

            entry.update({
                "status": "ok",
                "a_seconds": round(a_seconds, 1),
                "b_seconds": round(b_seconds, 1),
                "a_stats": graph_stats(state),
                "a_stop_reason": pipe_meta.get("stop_reason"),
                "a_grounding": pipe_meta.get("grounding"),
                "a_core_variables": core_names,
                "counterintuitiveness": (
                    round(counterintuitiveness(core_names, prior), 2) if prior else None
                ),
            })
        except Exception as exc:  # 单题失败不中断整场实验
            logger.exception("bench: q%02d 失败", idx)
            entry.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        manifest["questions"].append(entry)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (out_dir / ".answers.json").write_text(
        json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (out_dir / "评分表.md").write_text(_scoring_sheet(questions), encoding="utf-8")
    logger.info("bench: 完成, 输出在 %s", out_dir)
    return manifest


def _scoring_sheet(questions: list[str]) -> str:
    """生成盲评评分表 (协议: docs/设计预期-修正版.md §四 H1)。"""
    lines = [
        "# H1 盲评评分表",
        "",
        "评委须知: 每题有 X.md 和 Y.md 两份分析报告, 来源已匿名化。",
        "请独立阅读后回答两个问题, 填 X / Y / 平。**评完所有题之前不要查看任何 "
        "`.key.json` 或 `.answers.json`。**",
        "",
        "- Q1: 哪份更有助于你写出对外发布的分析?",
        "- Q2: 哪份包含你事先没想到的有效原因?",
        "",
        "| 题号 | 问题 | Q1 (X/Y/平) | Q2 (X/Y/平) | 备注 |",
        "|------|------|------------|------------|------|",
    ]
    for idx, q in enumerate(questions, start=1):
        lines.append(f"| q{idx:02d} | {q} |  |  |  |")
    lines += [
        "",
        "判定 (设计预期-修正版 §四 H1): A 组综合胜率 ≤ 50% 即失败 (打平即失败);",
        "50%-65% 灰区, 分析败因后修订重测一次; > 65% 通过。",
    ]
    return "\n".join(lines)
