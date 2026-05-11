import json
import re
import time
from datetime import date, timedelta
from typing import Any, Callable

from explain_agent.core.types import AdapterQuery, Evidence
from explain_agent.graph.state import DimensionResult
from explain_agent.llm import LLMClient


QUERY_GEN_SYSTEM = """你是金融归因 agent 的查询关键词生成器。
基于维度配置 + 已有证据，生成下一轮要查询的关键词。

输出 JSON: {"keywords": ["k1", "k2", ...]}
只输出 JSON，最多 5 个关键词。
"""

EVAL_SYSTEM = """你是证据评估器。判断当前已有证据是否足够回答该维度的问题。

判定标准（满足任一即返回 sufficient=true）：
- 已有 ≥3 条与该维度直接相关的证据
- 已有 ≥1 条核心证据可直接支撑结论（例如：政策维度有明确文件、资金面维度有具体净流入数据）
- 当前证据已覆盖该维度的主要面向，进一步检索的边际收益较低

只在证据明显不足且方向不清时才返回 sufficient=false。

输出 JSON: {"sufficient": true|false, "relevant_ids": ["id1", ...]}
只输出 JSON。
"""

SUMMARY_SYSTEM = """你是该维度的 mini 摘要器。基于相关证据写一段 150-300 字的维度内归因。
不要编造没有证据的结论。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class DimensionWorker:
    def __init__(self, dimension_config: dict, worker_config: dict,
                 llm: LLMClient, adapter_registry: dict[str, Any],
                 on_done: Callable[..., None] | None = None,
                 on_round: Callable[..., None] | None = None):
        self.dim = dimension_config
        self.cfg = worker_config
        self.llm = llm
        self.registry = adapter_registry
        self.on_done = on_done
        self.on_round = on_round

    async def run(
        self,
        target: str,
        time_window: tuple[date, date],
        market_facts: dict,
    ) -> DimensionResult:
        max_rounds = self.cfg.get("max_rounds", 10)
        soft_terminate = self.cfg.get("soft_terminate_no_gain_rounds", 2)

        t0 = time.perf_counter()
        all_evidence: list[Evidence] = []
        rounds = 0
        no_gain_count = 0

        for round_idx in range(1, max_rounds + 1):
            rounds = round_idx
            round_t0 = time.perf_counter()
            keywords = self._gen_keywords(target, time_window, market_facts, all_evidence)
            new_ev = await self._fetch_all_sources(keywords, target, time_window)
            new_ids = {e.id for e in new_ev} - {e.id for e in all_evidence}
            all_evidence.extend([e for e in new_ev if e.id in new_ids])

            if not new_ids:
                no_gain_count += 1
                self._emit_round(round_idx, max_rounds, keywords, len(new_ids),
                                 len(all_evidence), time.perf_counter() - round_t0,
                                 reason="no_gain")
                if no_gain_count >= soft_terminate:
                    break
                continue
            no_gain_count = 0

            sufficient = self._is_sufficient(all_evidence, target)
            self._emit_round(round_idx, max_rounds, keywords, len(new_ids),
                             len(all_evidence), time.perf_counter() - round_t0,
                             reason="sufficient" if sufficient else "continue")
            if sufficient:
                break

        if not all_evidence:
            result = DimensionResult(
                evidence=[], mini_summary="本维度未检索到相关证据",
                retry_count=rounds, no_data=True, confidence="low",
            )
            self._emit_done(t0, result)
            return result

        summary = self._summarize(all_evidence, target, market_facts)
        confidence = self._estimate_confidence(all_evidence)
        result = DimensionResult(
            evidence=all_evidence, mini_summary=summary,
            retry_count=rounds, no_data=False, confidence=confidence,
        )
        self._emit_done(t0, result)
        return result

    def _emit_round(self, round_idx: int, max_rounds: int, keywords: list,
                    new_count: int, total_count: int, duration: float,
                    reason: str) -> None:
        if self.on_round is None:
            return
        try:
            self.on_round(
                dim_id=self.dim.get("id", ""),
                round_idx=round_idx,
                max_rounds=max_rounds,
                keywords=keywords[:3],
                new_count=new_count,
                total_count=total_count,
                duration=duration,
                reason=reason,
            )
        except Exception:
            pass

    def _emit_done(self, t0: float, result: DimensionResult) -> None:
        if self.on_done is None:
            return
        try:
            self.on_done(
                dim_id=self.dim.get("id", ""),
                duration=time.perf_counter() - t0,
                no_data=result["no_data"],
                retry_count=result["retry_count"],
                evidence_count=len(result["evidence"]),
            )
        except Exception:
            pass

    def _gen_keywords(
        self, target: str, time_window: tuple[date, date],
        market_facts: dict, existing: list[Evidence],
    ) -> list[str]:
        existing_titles = "; ".join(e.title or e.snippet[:50] for e in existing[-5:])
        user = (
            f"维度: {self.dim['name']}\n"
            f"查询模板: {self.dim['query_template'].format(target=target, time_window=time_window)}\n"
            f"市场锚点: {market_facts.get('snippet', '')}\n"
            f"已有证据(最近 5 条): {existing_titles or '无'}"
        )
        raw = self.llm.chat(system=QUERY_GEN_SYSTEM, user=user, max_tokens=2000)
        data = _extract_json(raw)
        return data.get("keywords", [target])[:5] if data else [target]

    async def _fetch_all_sources(
        self, keywords: list[str], target: str, time_window: tuple[date, date],
    ) -> list[Evidence]:
        out = await self._fetch_once(keywords, target, time_window)
        if not out and (time_window[1] - time_window[0]).days <= 3:
            expanded = (time_window[1] - timedelta(days=6), time_window[1])
            out = await self._fetch_once(keywords, target, expanded)
        return out

    async def _fetch_once(
        self, keywords: list[str], target: str, time_window: tuple[date, date],
    ) -> list[Evidence]:
        out: list[Evidence] = []
        for src_name in self.dim["data_sources"]:
            if src_name not in self.registry:
                continue
            adapter = self.registry[src_name]
            q = AdapterQuery(
                keywords=keywords, time_window=time_window, target=target, limit=20,
            )
            try:
                out.extend(await adapter.query(q))
            except Exception:
                continue
        return out

    def _is_sufficient(self, evidence: list[Evidence], target: str) -> bool:
        snippets = "\n".join(f"id={e.id}: {e.snippet[:200]}" for e in evidence[:20])
        user = f"维度: {self.dim['name']}\n标的: {target}\n证据:\n{snippets}"
        raw = self.llm.chat(system=EVAL_SYSTEM, user=user, max_tokens=2000)
        data = _extract_json(raw)
        return bool(data and data.get("sufficient", False))

    def _summarize(self, evidence: list[Evidence], target: str, market_facts: dict) -> str:
        snippets = "\n".join(f"id={e.id}: {e.snippet[:300]}" for e in evidence)
        user = (
            f"维度: {self.dim['name']}\n标的: {target}\n"
            f"市场锚点: {market_facts.get('snippet', '')}\n证据:\n{snippets}"
        )
        return self.llm.chat(system=SUMMARY_SYSTEM, user=user, max_tokens=2000)

    def _estimate_confidence(self, evidence: list[Evidence]) -> str:
        n = len(evidence)
        if n >= 5:
            return "high"
        if n >= 2:
            return "medium"
        return "low"
