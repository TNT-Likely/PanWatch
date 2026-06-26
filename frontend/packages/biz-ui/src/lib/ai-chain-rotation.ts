/** AI 行情轮动：沿物理连接顺序，颜色与关注页卡片/标签统一 */

export type ChainLayerKey =
  | 'gpu'
  | 'cpo'
  | 'hbm'
  | 'pcb'
  | 'liquid_cooling'
  | 'semi_pcb_equip'
  | 'server'
  | 'idc'
  | 'power'
  | 'cloud_llm'
  | 'software_app'
  | 'physical_ai'
  | 'other'

export interface ChainLayerTheme {
  key: ChainLayerKey
  label: string
  badge: string
  /** 关注卡片：左侧色条 + 浅底色 */
  card: string
  /** 精华 + 轮动色 */
  cardFeatured: string
  /** 时间轴节点 */
  dot: string
  /** 时间轴节点描边/光晕 */
  dotRing: string
  /** 跳过环节（A股） */
  skipAshare?: boolean
}

export const CHAIN_LAYER_LEGACY_MAP: Record<string, string> = {
  upstream: 'gpu',
  midstream: 'cloud_llm',
  downstream: 'physical_ai',
  foundation: 'semi_pcb_equip',
  middleware: 'cloud_llm',
  integration: 'idc',
  application: 'physical_ai',
}

export const CHAIN_LAYER_ORDER: Record<string, number> = {
  gpu: 1,
  cpo: 2,
  hbm: 3,
  pcb: 4,
  liquid_cooling: 5,
  semi_pcb_equip: 6,
  server: 7,
  idc: 8,
  power: 9,
  cloud_llm: 10,
  software_app: 11,
  physical_ai: 12,
  other: 99,
}

/** 当前轮动热点 */
export const CHAIN_HOT_LAYER: ChainLayerKey = 'semi_pcb_equip'

/** 算力链下一站 */
export const CHAIN_NEXT_LAYER: ChainLayerKey = 'server'

/** A股跳过云/软件后的主题方向 */
export const CHAIN_THEME_LAYER: ChainLayerKey = 'physical_ai'

export const AI_COMPUTE_STEPS: ChainLayerTheme[] = [
  {
    key: 'gpu',
    label: 'GPU',
    badge: 'bg-sky-500/15 text-sky-600',
    card: 'border-l-[3px] border-l-sky-500 bg-sky-500/[0.05] hover:bg-sky-500/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-sky-500 bg-sky-500/[0.10] hover:bg-sky-500/[0.14] border-amber-500/40',
    dot: 'bg-sky-500',
    dotRing: 'ring-sky-500/35',
  },
  {
    key: 'cpo',
    label: 'CPO',
    badge: 'bg-sky-600/15 text-sky-700',
    card: 'border-l-[3px] border-l-sky-600 bg-sky-600/[0.05] hover:bg-sky-600/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-sky-600 bg-sky-600/[0.07] hover:bg-sky-600/[0.11] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-sky-600',
    dotRing: 'ring-sky-600/35',
  },
  {
    key: 'hbm',
    label: 'HBM',
    badge: 'bg-indigo-500/15 text-indigo-600',
    card: 'border-l-[3px] border-l-indigo-500 bg-indigo-500/[0.05] hover:bg-indigo-500/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-indigo-500 bg-indigo-500/[0.07] hover:bg-indigo-500/[0.11] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-indigo-500',
    dotRing: 'ring-indigo-500/35',
  },
  {
    key: 'pcb',
    label: 'PCB',
    badge: 'bg-violet-500/15 text-violet-600',
    card: 'border-l-[3px] border-l-violet-500 bg-violet-500/[0.05] hover:bg-violet-500/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-violet-500 bg-violet-500/[0.07] hover:bg-violet-500/[0.11] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-violet-500',
    dotRing: 'ring-violet-500/35',
  },
  {
    key: 'liquid_cooling',
    label: '液冷',
    badge: 'bg-violet-600/15 text-violet-700',
    card: 'border-l-[3px] border-l-violet-600 bg-violet-600/[0.05] hover:bg-violet-600/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-violet-600 bg-violet-600/[0.07] hover:bg-violet-600/[0.11] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-violet-600',
    dotRing: 'ring-violet-600/35',
  },
  {
    key: 'semi_pcb_equip',
    label: '材料&设备',
    badge: 'bg-amber-500/15 text-amber-600',
    card: 'border-l-[3px] border-l-amber-500 bg-amber-500/[0.06] hover:bg-amber-500/[0.10] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-amber-500 bg-amber-500/[0.09] hover:bg-amber-500/[0.13] border-amber-500/30 ring-1 ring-amber-500/15',
    dot: 'bg-amber-500',
    dotRing: 'ring-amber-500/50',
  },
  {
    key: 'server',
    label: '服务器',
    badge: 'bg-orange-500/15 text-orange-600',
    card: 'border-l-[3px] border-l-orange-500 bg-orange-500/[0.06] hover:bg-orange-500/[0.10] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-orange-500 bg-orange-500/[0.09] hover:bg-orange-500/[0.13] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-orange-500',
    dotRing: 'ring-orange-500/45',
  },
  {
    key: 'idc',
    label: 'IDC',
    badge: 'bg-orange-600/15 text-orange-700',
    card: 'border-l-[3px] border-l-orange-600 bg-orange-600/[0.05] hover:bg-orange-600/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-orange-600 bg-orange-600/[0.08] hover:bg-orange-600/[0.12] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-orange-600',
    dotRing: 'ring-orange-600/35',
  },
  {
    key: 'power',
    label: '电力',
    badge: 'bg-rose-500/15 text-rose-600',
    card: 'border-l-[3px] border-l-rose-500 bg-rose-500/[0.05] hover:bg-rose-500/[0.09] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-rose-500 bg-rose-500/[0.08] hover:bg-rose-500/[0.12] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-rose-500',
    dotRing: 'ring-rose-500/35',
  },
]

export const AI_POST_COMPUTE_PHASES: ChainLayerTheme[] = [
  {
    key: 'cloud_llm',
    label: '云&大模型',
    badge: 'bg-slate-500/15 text-slate-500',
    card: 'border-l-[3px] border-l-slate-400 bg-slate-500/[0.04] hover:bg-slate-500/[0.07] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-slate-400 bg-slate-500/[0.06] hover:bg-slate-500/[0.09] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-slate-400',
    dotRing: 'ring-slate-400/25',
    skipAshare: true,
  },
  {
    key: 'software_app',
    label: '软件应用',
    badge: 'bg-slate-500/15 text-slate-500',
    card: 'border-l-[3px] border-l-slate-400 bg-slate-500/[0.04] hover:bg-slate-500/[0.07] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-slate-400 bg-slate-500/[0.06] hover:bg-slate-500/[0.09] border-amber-500/25 ring-1 ring-amber-500/10',
    dot: 'bg-slate-400',
    dotRing: 'ring-slate-400/25',
    skipAshare: true,
  },
  {
    key: 'physical_ai',
    label: '物理AI',
    badge: 'bg-emerald-500/15 text-emerald-600',
    card: 'border-l-[3px] border-l-emerald-500 bg-emerald-500/[0.06] hover:bg-emerald-500/[0.10] border-border/40',
    cardFeatured: 'border-l-[3px] border-l-emerald-500 bg-emerald-500/[0.09] hover:bg-emerald-500/[0.13] border-amber-500/25 ring-1 ring-emerald-500/15',
    dot: 'bg-emerald-500',
    dotRing: 'ring-emerald-500/45',
  },
]

export const ALL_CHAIN_LAYER_THEMES: ChainLayerTheme[] = [
  ...AI_COMPUTE_STEPS,
  ...AI_POST_COMPUTE_PHASES,
]

const THEME_BY_KEY = Object.fromEntries(
  ALL_CHAIN_LAYER_THEMES.map((theme) => [theme.key, theme]),
) as Record<string, ChainLayerTheme>

export const CHAIN_LAYER_BADGE_STYLES: Record<string, string> = Object.fromEntries(
  ALL_CHAIN_LAYER_THEMES.map((theme) => [theme.key, theme.badge]),
)

export function normalizeChainLayer(layer?: string | null): string {
  if (!layer) return 'other'
  return CHAIN_LAYER_LEGACY_MAP[layer] || layer
}

/** 股票卡片标签：仅展示轮动环节名（GPU / CPO / 物理AI），不含「人工智能·」前缀 */
export function formatIndustryChainDisplay(chain?: {
  layer?: string | null
  layer_label?: string | null
  display?: string | null
  sector?: string | null
} | null): string {
  if (!chain?.layer) return ''
  if (chain.layer === 'other' || chain.sector === 'OTHER') return '其他'
  const theme = getChainLayerTheme(chain.layer)
  if (theme?.label) return theme.label
  const label = (chain.layer_label || chain.display || '').trim()
  if (!label) return chain.layer
  // 兼容 DB 中旧版「人工智能·底层」格式
  const dot = label.indexOf('·')
  return dot >= 0 ? label.slice(dot + 1).trim() || label : label
}

export function getChainLayerTheme(layer?: string | null): ChainLayerTheme | null {
  const key = normalizeChainLayer(layer)
  return THEME_BY_KEY[key] ?? null
}

export function chainLayerFilterKey(layerKey: string, sector = 'AI'): string {
  return `${sector}:${layerKey}`
}

export function watchlistCardChainClass(
  layer?: string | null,
  isFeatured = false,
): string {
  const featuredAccent =
    'border-amber-500/55 ring-2 ring-amber-500/30 shadow-md shadow-amber-500/15'
  const theme = getChainLayerTheme(layer)
  if (!theme) {
    return isFeatured
      ? `border-l-[4px] border-l-amber-500 bg-gradient-to-br from-amber-500/20 via-amber-400/10 to-amber-500/5 hover:from-amber-500/25 ${featuredAccent}`
      : 'border-border/40 bg-background/30 hover:bg-accent/20'
  }
  return isFeatured
    ? `${theme.cardFeatured} ${featuredAccent}`
    : theme.card
}
