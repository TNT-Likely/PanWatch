from src.modules.automation.base import AgentContext, AnalysisResult, BaseAgent


class _SampleAgent(BaseAgent):
    name = "daily_report"
    display_name = "盘后日报"

    async def collect(self, context):
        return {}

    def build_prompt(self, data, context):
        return "Analyze the session.", ""


def test_agent_context_defaults_to_simplified_chinese():
    context = AgentContext(ai_client=None, notifier=None, config=None)
    assert context.report_language == "zh-CN"


def test_english_report_language_adds_output_instruction_and_localizes_title():
    context = AgentContext(
        ai_client=None,
        notifier=None,
        config=None,
        report_language="en-US",
    )
    agent = _SampleAgent()
    prompt = agent.apply_report_language(context, "Analyze the session.")
    result = AnalysisResult(
        agent_name="daily_report",
        title="【盘后日报】贵州茅台、平安银行 等2只",
        content="",
    )

    agent.localize_result_title(result, context)

    assert "Output language: English" in prompt
    assert result.title == "[Daily report] 贵州茅台, 平安银行 and 2 stocks"


def test_chinese_report_language_preserves_prompt_and_title():
    context = AgentContext(ai_client=None, notifier=None, config=None)
    agent = _SampleAgent()
    result = AnalysisResult(agent_name="daily_report", title="【盘后日报】贵州茅台", content="")

    assert agent.apply_report_language(context, "提示词") == "提示词"
    agent.localize_result_title(result, context)
    assert result.title == "【盘后日报】贵州茅台"
