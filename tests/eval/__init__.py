"""Agent 过程评测集（golden set + 规则断言 + LLM-as-judge 框架）。

与业务后验评测（factor_eval 等"评结果"）互补，这里"评过程"：
工具选择/参数/有据性/结构化输出可解析/动作白名单。

- 纯规则用例（structured_output 解析）随 make test 常跑；
- 需要真实模型的 chat 工具循环用例通过 make eval 运行（配置见 run_eval.py）。
"""
