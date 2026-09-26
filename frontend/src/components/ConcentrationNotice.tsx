export function ConcentrationNotice({ item }: { item: {
  concentration_flag?: boolean
  concentration_note?: string
  raw_score?: number
  raw_rank_score?: number
} }) {
  if (!item.concentration_note) return null
  return <span title={item.concentration_note} className="inline-block rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600">
    {item.concentration_flag ? `集中度降权 · 原分 ${item.raw_rank_score ?? item.raw_score ?? '—'}` : '集中度暂不可用'}
  </span>
}
