"""LLM 调用相关异常。

设计:
- LLMError: 底层调用错误（网络 / API / 超时 / rate limit / JSON 解析失败）。
- SchemaValidationError: LLM 输出格式合法但不 fit Pydantic schema。
  典型: structured output 缺字段或字段类型不对。retry 1 次仍失败后抛出。

JSON 解析失败归 LLMError 而非 SchemaValidationError —— SchemaValidationError 专指
"格式 OK 但内容不符 schema"，JSONDecodeError 属于格式坏，归 parse-layer。
"""


class LLMError(Exception):
    """网络 / API / 超时 / rate limit / JSON 解析等底层调用错误。

    所有 provider client 把底层 SDK 异常 wrap 成此类。
    """


class SchemaValidationError(Exception):
    """LLM 输出不 fit Pydantic schema。

    retry 1 次仍失败后抛出。
    """
