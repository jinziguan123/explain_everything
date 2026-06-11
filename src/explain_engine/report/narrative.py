"""叙事报告生成 — 设计预期-修正版 §九 Phase V 交付物。

generate_report 调 LLM 把图谱档案写成多层级叙事报告。约束:
1. 只允许使用档案内容 (防伪深刻的执行机制);
2. 关键断言必须带节点 id 引用;
3. 措辞按认知等级分级 (实证/观察/推断/解释性假设/猜测);
4. 必须如实呈现弱链与解释残差, 不允许把图的洞修饰平。
"""

from __future__ import annotations

from explain_engine.llm.client import LLMClient, Message
from explain_engine.report.dossier import build_dossier
from explain_engine.schema.state import CognitiveState

REPORT_INSTRUCTIONS = """\
你是一名严谨的因果分析报告撰写者。下面是一份"图谱档案"——一个认知引擎对某个\
"为什么"问题收敛后的全部产出。请把它写成一份面向深度内容生产者 (研究者/分析师/\
专栏作者) 的中文叙事报告。

硬约束 (违反任何一条即失败):
1. 你只能使用档案中出现的变量、关系、机制描述与证据附录, 不得引入档案之外的\
任何原因、数据或例子。档案没说的, 就写"图谱未覆盖"。
2. 每个关键断言后面用方括号标注它依赖的节点 id, 例如 [c_001]。
3. 措辞必须与档案中的"认知等级"对齐 (四级证据状态机):
   - 实证 → 直陈 ("检索到的来源显示…"), 并附证据附录中的来源链接;
   - 推断 → "由此可推" (它由已实证的机制生成, 但自身无直接证据);
   - 假设 → "一个未经验证的可能是" (LLM 提出、检索未支持也未否定);
   - 争议 → 必须同时呈现支持与反驳两说, 并写明"来源相互冲突", 不许擇一。
   不允许把假设写成事实。
4. 档案标注了"⚠ 弱链"或"解释残差"的内容必须在第六节如实呈现。
5. 若档案声明"证据接地: 未执行", 必须在报告开头注明本报告全部内容未经外部验证。

报告结构 (用这些标题):
# 解释报告: <问题>
## 一、结论概要 (200 字内, 给出最核心的解释结构)
## 二、表层诱因 (现象之间最直接的关联)
## 三、深层机制 (L1/L2 变量如何生成这些现象, 按因果链展开)
## 四、反直觉点 (核心变量中哪些超出了先验预期; 若档案无先验对照则写"未做先验对照")
## 五、置信度分级 (按认知等级把主要结论分为: 较可靠 / 推断 / 待验证三档)
## 六、边界与未解释残差 (弱链、未覆盖的现象、本解释的适用边界)

直接输出 markdown 报告, 不要任何前言或解释。
"""


async def generate_report(
    state: CognitiveState,
    llm: LLMClient,
    prior_causes: list[str] | None = None,
    model: str | None = None,
) -> str:
    """从收敛的 CognitiveState 生成叙事报告 markdown (1 次 LLM call)。"""
    dossier = build_dossier(state, prior_causes=prior_causes)
    resp = await llm.chat(
        [Message(role="user", content=f"{REPORT_INSTRUCTIONS}\n\n---\n\n{dossier}")],
        model=model,
    )
    return resp.text.strip()
