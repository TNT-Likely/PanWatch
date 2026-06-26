export interface InvestmentProfileAddLevel {
  drawdown_pct: number
  budget_pct: number
}

export interface InvestmentProfile {
  long_term_enabled: boolean
  portfolio_role: 'core' | 'satellite' | 'watch' | string
  target_weight_pct: number | null
  max_weight_pct: number | null
  add_plan: {
    basis: string
    levels: InvestmentProfileAddLevel[]
  }
  reduce_plan: {
    take_profit_pct: number
    scope: string
  }
  thesis: string
  thesis_invalidations: string[]
}

export interface InvestmentProfileEvaluateResult {
  stock_id: number
  symbol: string
  market: string
  current_price: number | null
  eligible: boolean
  current_drawdown_pct: number
  weight_pct: number
  triggered_level: InvestmentProfileAddLevel | null
  next_level: InvestmentProfileAddLevel | null
  next_trigger_price: number | null
  suggested_amount: number
  suggested_qty: number
  blockers: string[]
  summary: string
  profile: InvestmentProfile
}

export const DEFAULT_INVESTMENT_PROFILE: InvestmentProfile = {
  long_term_enabled: false,
  portfolio_role: 'watch',
  target_weight_pct: null,
  max_weight_pct: null,
  add_plan: {
    basis: 'avg_cost',
    levels: [
      { drawdown_pct: -5, budget_pct: 20 },
      { drawdown_pct: -10, budget_pct: 30 },
      { drawdown_pct: -15, budget_pct: 50 },
    ],
  },
  reduce_plan: {
    take_profit_pct: 15,
    scope: 'satellite_only',
  },
  thesis: '',
  thesis_invalidations: [],
}
