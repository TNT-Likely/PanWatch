import type { TourTrack } from '@panwatch/api/onboarding'

export type FeatureTrack = Exclude<TourTrack, 'main'>
export const featureTracks: FeatureTrack[] = ['opportunities', 'paper', 'assistant', 'automation', 'datasources']
export type FeatureStep = { key: string; path: string; selector: string; fallback?: string; dialog?: string }
export const trackSteps: Record<FeatureTrack, FeatureStep[]> = {
  opportunities: [
    { key: 'filters', path: '/opportunities', selector: '[data-tour="opportunity-filters"]' },
    { key: 'signals', path: '/opportunities', selector: '[data-tour="opportunity-signals"]' },
    { key: 'evaluation', path: '/evaluations', selector: '[data-tour="evaluations"]' },
  ],
  paper: [
    { key: 'capital', path: '/paper-trading', selector: '[data-tour="paper-capital"]', dialog: '[data-tour="paper-config-dialog"]' },
    { key: 'controls', path: '/paper-trading', selector: '[data-tour="paper-controls"]' },
    { key: 'positions', path: '/paper-trading', selector: '[data-tour="paper-positions"]' },
  ],
  assistant: [
    { key: 'composer', path: '/assistant', selector: '[data-tour="assistant-composer"], [data-testid="assistant-composer"]' },
    { key: 'context', path: '/assistant', selector: '[data-tour="assistant-permissions"]', dialog: '[data-tour="assistant-permissions-dialog"]' },
    { key: 'activity', path: '/assistant', selector: '[data-tour="assistant-toolbar"]' },
  ],
  automation: [
    { key: 'schedule', path: '/agents', selector: '[data-tour="agent-schedule"]', dialog: '[data-tour="agent-schedule-dialog"]' },
    { key: 'binding', path: '/agents', selector: '[data-tour="agent-binding"]', dialog: '[data-tour="agent-binding-dialog"]' },
    { key: 'history', path: '/history', selector: '[data-tour="analysis-history"]' },
  ],
  datasources: [
    { key: 'catalog', path: '/datasources', selector: '[data-tour="source-category"]' },
    { key: 'config', path: '/datasources', selector: '[data-tour="source-config"]', fallback: '[data-tour="source-category"]', dialog: '[data-tour="source-config-dialog"]' },
    { key: 'test', path: '/datasources', selector: '[data-tour="source-test"]:not(:disabled)', dialog: '[data-tour="source-test-dialog"]' },
  ],
}
