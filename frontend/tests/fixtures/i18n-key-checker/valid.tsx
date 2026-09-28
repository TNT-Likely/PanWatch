declare function useTranslation(namespace: string): {
  t: (key: string, options?: Record<string, unknown>) => string
}

declare const market: string

const { t } = useTranslation('configuration')
const opportunityT = (key: string, options?: Record<string, unknown>) =>
  t(`opportunities.${key}`, options)

opportunityT('errors.timeout')
opportunityT(`markets.${market}`)

