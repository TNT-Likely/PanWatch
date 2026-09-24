"""盘前决策流水线(premarket_pipeline)全部阈值的唯一事实源。

流水线各阶段(宏观三卡/板块预测/选股三价/财报核验/落库推送)只从本模块读常量,
prompt 占位符也由本模块注入,禁止在阶段实现里散落魔法数。

与仓库数据可靠性约定一致:
- 每个常量都带注释说明业务含义与单位;
- 数值改动只发生在本文件,阶段代码不自带默认值。
"""

from __future__ import annotations

from pathlib import Path

# ────────────────────────── Agent 身份 ──────────────────────────

AGENT_NAME = "premarket_pipeline"
AGENT_LABEL = "盘前决策流水线"
#: 建议池/信号直写用的 strategy_code 与 agent_name 同名
STRATEGY_CODE = "premarket_pipeline"
#: StrategySignalRun 直写哨兵:source_candidate_id 用 0 表示"非 EntryCandidate 产物",
#: refresh_strategy_signals 的 existing 索引会跳过 0 哨兵行(防覆盖)。
SENTINEL_CANDIDATE_ID = 0

# ────────────────────────── 调度与超时(秒/分钟) ──────────────────────────

#: 默认实例化参数(与 agent_catalog AGENT_SEED_SPECS 的 config 保持一致)
DEFAULT_PIPELINE_TIMEOUT_MINUTES = 20
DEFAULT_LLM_TIMEOUT_SECONDS = 120
DEFAULT_EMIT_PAPER_TRADING_SIGNAL = False
DEFAULT_AUTO_CREATE_ALERTS = True
DEFAULT_MAX_CANDIDATES = 5
DEFAULT_BOARD_TOP_N = 6
DEFAULT_MV_MIN_E8 = 50      # 流通市值下限,亿
DEFAULT_MV_MAX_E8 = 3000    # 流通市值上限,亿

#: 财报按需拉取总预算:剩余时间低于该秒数时跳过后续候选(标 budget_exhausted)
FINANCE_BUDGET_RESERVE_SEC = 60
#: akshare 财务摘要按需拉取的调用间隔(限频保护)
AKSHARE_FETCH_INTERVAL_SEC = 1.0
#: 单次 akshare 外呼硬超时(秒):动量K线/财务摘要/核验多源抓取共用
AKSHARE_FETCH_TIMEOUT_SEC = 20

# ────────────────────────── 阶段1 宏观三卡 ──────────────────────────

#: 事件日历窗口:近 N 天 + 未来 N 个交易日;窗口内无任何事件 = STOP 硬停
CALENDAR_PAST_DAYS = 7
CALENDAR_FUTURE_TRADING_DAYS = 5
#: 近 N 天事件切片(报告「事件对表」之外的补充上下文)
EVENTS_RECENT_DAYS = 3
#: 可用通道数门禁:低于该值只产数据快照报告,不产出三卡
MIN_AVAILABLE_CHANNELS = 2
#: 事件/海外通道弱(不可用或降级)时,三卡置信度上限
WEAK_CHANNEL_CONF_CAP = 4
#: 三卡 position_policy.diff.direction 的合法取值;出现 relax 即废卡重写
POLICY_DIFF_DIRECTIONS = ("tighten", "relax")
#: 废卡重写最大尝试次数(首次 + 一次带失败原因的重试)
CARD_REWRITE_MAX_ATTEMPTS = 2

# ────────────────────────── 阶段2 板块预测 ──────────────────────────

#: 动量合成权重:5/10/20 日涨幅加权(和为 1)
MOMENTUM_WEIGHTS = {"d5": 0.5, "d10": 0.3, "d20": 0.2}
#: 热度阈值:行业涨停家数 / 概念涨停家数 / 全市场涨停总数(超过记热度达标)
HEAT_LIMIT_UP = {"industry": 3, "concept": 5, "market_total": 50}
#: 背离判定:涨>2% 且主力净流出>5亿 = 派发;跌>2% 且主力净流入>3亿 = 吸筹
DIVERGENCE = {
    "rally_pct": 2.0,          # 涨幅阈,%
    "rally_outflow_e8": 5.0,   # 主力净流出阈,亿(取绝对值比较)
    "drop_pct": -2.0,          # 跌幅阈,%
    "drop_inflow_e8": 3.0,     # 主力净流入阈,亿
}
#: 四阶段矩阵(蓄力/突破/加速/衰竭),单位:% 或 亿
STAGE_MATRIX = {
    "蓄力": {"d5_max": 2.0, "d20_min": -5.0, "require_small_inflow": True},
    "突破": {"d5_min": 3.0, "require_main_inflow": True},
    "加速": {"d5_min": 5.0, "require_limit_up_wave": True},
    "衰竭": {"require_momentum_fading": True, "require_main_outflow": True},
}
#: 板块初筛送 LLM 修正的数量上限
BOARD_TOP_N_DEFAULT = DEFAULT_BOARD_TOP_N

# ────────────────────────── 阶段3 选股三价 ──────────────────────────

#: 粗排截断:每板块成分按涨幅/资金流排序后最多进入硬过滤的数量
COARSE_PICK_TOP_N = 30
#: 上市天数下限(自然日)
LIST_AGE_MIN_DAYS = 60
#: 三高评分:维度间权重(增长/利润/壁垒)与龙头合成(三高/政策关联)
TRIPLE_HIGH = {
    "dimension_weights": {"growth": 0.35, "profit": 0.35, "moat": 0.30},
    "leader_weights": {"triple_high": 0.6, "policy": 0.4},
    # 维度内指标权重(缺项按剩余权重归一)
    "growth_indicators": {"rev_cagr": 0.4, "profit_cagr": 0.6},
    "profit_indicators": {"gross_margin": 0.4, "net_margin": 0.3, "roe": 0.3},
    "moat_indicators": {"gross_margin": 0.6, "roe": 0.4},
}
#: 缺项降级:5 项指标缺 <=2 项(即 >=3/5)取均值;缺 3 项(2/5)总分乘 0.9;
#: 再缺(<2/5)弃维度并标注
TRIPLE_HIGH_MISSING_RULES = {
    "full_max_missing": 2,     # 缺项 <=2 → 正常均值
    "degraded_missing": 3,     # 缺项 ==3 → 乘 0.9
    "degrade_factor": 0.9,
}
#: 一票否决:连续 N 期归母净利为负 / ST 名称
VETO_CONSECUTIVE_LOSS_PERIODS = 2
#: 评级档位:按龙头总分划 A/B/C,对应不同止损
GRADE_THRESHOLDS = {"A": 0.80, "B": 0.65}
#: 止损:评级对应从入场中值回撤的比例(负值)
STOP_LOSS_BY_GRADE = {"A": -0.15, "B": -0.12, "C": -0.08}
#: 止盈:+30% 与近端压力位孰低
TAKE_PROFIT_PCT = 0.30
#: 近端压力位窗口(日 K 高点)
NEAR_RESISTANCE_WINDOW_DAYS = 60
#: V11 硬校验:(target - 入场中值) / (入场中值 - stop) 的下限,不过则弃
RR_MIN = 2.0
#: PE 百分位窗口(年)与最低样本数;不足降级技术面支撑
PE_WINDOW_YEARS = 5
PE_MIN_OBS = 120
#: 技术面支撑降级口径:入场下轨=近 N 日低点,上轨=20 日均线
TECH_FALLBACK_LOW_WINDOW_DAYS = 60
TECH_FALLBACK_MA_DAYS = 20
#: 政策关联打分(无事件命中时给中性缺省,宁缺勿编)
POLICY_SCORE = {"event_high_hit": 1.0, "event_medium_hit": 0.7, "default": 0.5}

# ────────────────────────── 阶段4 财报核验 ──────────────────────────

#: 披露新鲜度:最新报告期距今天数上限(约两个季度)
DISCLOSURE_FRESH_MAX_DAYS = 190
#: EPS 锚突变判定界(相邻可比期 EPS 比值越界即判除权/拆股,锚失效强制最新 TTM)。
#: 这是启发式:PanWatch 暂无逐笔分红/拆股数据源,不能声称覆盖精确的 60 天窗口。
EPS_ANCHOR_MUTATION_BOUNDS = (0.4, 2.5)
#: 未来 N 个交易日内有预约披露 → warn 降置信度(不剔除)
DISCLOSURE_WINDOW_TRADING_DAYS = 10
#: 双源交叉容差(营收/净利同比/EPS 偏差上限,相对值)
DUAL_SOURCE_TOLERANCE = 0.05
#: 财务摘要多源通道(按优先级;任两路可得即可交叉)
FUND_SOURCE_PRIORITY = ("em", "sina", "ths")

# ────────────────────────── 阶段5 落库推送 ──────────────────────────

#: 提醒规则命名前缀("[盘前 YYYY-MM-DD] ..."),同日重跑先按前缀删除
ALERT_NAME_PREFIX = "[盘前 {date}]"
#: 提醒规则过期时点(当日 15:30,本地时区)
ALERT_EXPIRE_TIME = "15:30"
#: 提醒冷却(分钟)
ALERT_COOLDOWN_MINUTES = 30
#: 每条提醒每日最多触发
ALERT_MAX_TRIGGERS_PER_DAY = 3
#: 建议有效期(小时):盘前流水线当日有效
SUGGESTION_EXPIRES_HOURS = 12
#: 预测回评 horizon(交易日)
PREDICTION_HORIZONS = (1, 3, 5, 10)
#: 建议池 agent_label
SUGGESTION_AGENT_LABEL = "盘前流水线"

# ────────────────────────── 置信度公式(prompt 共用) ──────────────────────────

CONFIDENCE_FORMULA = {
    "base": {"HIGH": 5, "MED": 3, "LOW": 2},   # 证据强度基础分
    "continuity_bonus": 1,                     # 数据连续性加分上限
    "three_questions_max": 3,                  # 三问通过率满分
    "max_score": 10,
    "event_window_penalty": 2,                 # 事件窗内扣减
    "decision_threshold": 5,                   # <5 前缀"不建议作为决策依据"
    "no_source_cap": 6.5,                      # 无来源评分上限
}

# ────────────────────────── prompt 文件 ──────────────────────────

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
PROMPT_MACRO = "premarket_pipeline_macro.txt"
PROMPT_SECTOR = "premarket_pipeline_sector.txt"
PROMPT_PICK = "premarket_pipeline_pick.txt"

#: 输出契约标签(structured_output 同款)
TAG_START = "<!--PANWATCH_JSON-->"
TAG_END = "<!--/PANWATCH_JSON-->"


def render_prompt(name: str, extra: dict | None = None) -> str:
    """读取 prompt 模板并把 {{TOKEN}} 占位符替换为本模块配置值。

    extra 里的键优先(运行期少量动态值,如板块列表行数);未知占位符原样保留,
    便于发现模板与配置的脱节。
    """
    text = (PROMPT_DIR / name).read_text(encoding="utf-8")
    tokens: dict[str, str] = {k: str(v) for k, v in prompt_tokens().items()}
    if extra:
        tokens.update({k: str(v) for k, v in extra.items()})
    for key, value in tokens.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def prompt_tokens() -> dict:
    """prompt 占位符 → 配置值映射(唯一出处,改阈值即改 prompt)。"""
    return {
        "MOMENTUM_D5_W": MOMENTUM_WEIGHTS["d5"],
        "MOMENTUM_D10_W": MOMENTUM_WEIGHTS["d10"],
        "MOMENTUM_D20_W": MOMENTUM_WEIGHTS["d20"],
        "HEAT_INDUSTRY": HEAT_LIMIT_UP["industry"],
        "HEAT_CONCEPT": HEAT_LIMIT_UP["concept"],
        "HEAT_MARKET": HEAT_LIMIT_UP["market_total"],
        "DIV_RALLY_PCT": DIVERGENCE["rally_pct"],
        "DIV_RALLY_OUTFLOW_E8": DIVERGENCE["rally_outflow_e8"],
        "DIV_DROP_PCT": DIVERGENCE["drop_pct"],
        "DIV_DROP_INFLOW_E8": DIVERGENCE["drop_inflow_e8"],
        "STOP_A_PCT": abs(STOP_LOSS_BY_GRADE["A"]) * 100,
        "STOP_B_PCT": abs(STOP_LOSS_BY_GRADE["B"]) * 100,
        "STOP_C_PCT": abs(STOP_LOSS_BY_GRADE["C"]) * 100,
        "TAKE_PROFIT_PCT": TAKE_PROFIT_PCT * 100,
        "RR_MIN": RR_MIN,
        "MV_MIN_E8": DEFAULT_MV_MIN_E8,
        "MV_MAX_E8": DEFAULT_MV_MAX_E8,
        "LIST_AGE_MIN_DAYS": LIST_AGE_MIN_DAYS,
        "COARSE_PICK_TOP_N": COARSE_PICK_TOP_N,
        "TH_GROWTH_W": TRIPLE_HIGH["dimension_weights"]["growth"],
        "TH_PROFIT_W": TRIPLE_HIGH["dimension_weights"]["profit"],
        "TH_MOAT_W": TRIPLE_HIGH["dimension_weights"]["moat"],
        "LEADER_TH_W": TRIPLE_HIGH["leader_weights"]["triple_high"],
        "LEADER_POLICY_W": TRIPLE_HIGH["leader_weights"]["policy"],
        "CONF_BASE_HIGH": CONFIDENCE_FORMULA["base"]["HIGH"],
        "CONF_BASE_MED": CONFIDENCE_FORMULA["base"]["MED"],
        "CONF_BASE_LOW": CONFIDENCE_FORMULA["base"]["LOW"],
        "CONF_CONTINUITY_BONUS": CONFIDENCE_FORMULA["continuity_bonus"],
        "CONF_THREE_Q_MAX": CONFIDENCE_FORMULA["three_questions_max"],
        "CONF_MAX": CONFIDENCE_FORMULA["max_score"],
        "CONF_EVENT_PENALTY": CONFIDENCE_FORMULA["event_window_penalty"],
        "CONF_DECISION_THRESHOLD": CONFIDENCE_FORMULA["decision_threshold"],
        "CONF_NO_SOURCE_CAP": CONFIDENCE_FORMULA["no_source_cap"],
        "DUAL_SOURCE_TOLERANCE_PCT": DUAL_SOURCE_TOLERANCE * 100,
        "TAG_START": TAG_START,
        "TAG_END": TAG_END,
    }
