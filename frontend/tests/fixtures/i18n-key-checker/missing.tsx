declare function useTranslation(namespace: string): {
  t: (key: string, options?: Record<string, unknown>) => string
}

declare const reason: string

const { t } = useTranslation('configuration')
const message = (key: string, options?: Record<string, unknown>) =>
  t(`p4.paperTrading.messages.${key}`, options)

t('errors.timeout')
message(`exitReasons.${reason}`)

