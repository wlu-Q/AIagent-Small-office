import { t } from '../../i18n'
export type VisualizationLanguage = 'mermaid' | 'office-chart'
export type ChartSpec = {
  type: 'bar' | 'line' | 'pie'
  title: string
  labels: string[]
  series: { name: string; values: (number | null)[] }[]
  unit?: string
  source?: string
}

export const MAX_VISUALIZATION_LENGTH = 12000

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('ui.chart_configuration_must_be_a_json_object'))
  return value as Record<string, unknown>
}

function keys(value: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new Error(t('ui.unsupported_configuration_fields_use_the_documented_chart_format'))
}

function label(value: unknown, field: string, max = 80): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(t('ui.value_must_contain_1_value_characters', { value1: (field), value2: (max) }))
  return value.trim()
}

function checkLength(source: string) {
  if (source.length > MAX_VISUALIZATION_LENGTH) throw new Error(t('ui.chart_content_is_too_long_split_it_into_smaller_charts'))
}

// This is a data contract, never a pass-through ECharts option or executable code.
export function parseChart(source: string): ChartSpec {
  checkLength(source)
  let data: Record<string, unknown>
  try { data = object(JSON.parse(source)) } catch { throw new Error(t('ui.invalid_chart_json_check_quotes_commas_and_brackets')) }
  keys(data, ['type', 'title', 'labels', 'series', 'unit', 'source'])
  if (data.type !== 'bar' && data.type !== 'line' && data.type !== 'pie') throw new Error(t('ui.supported_types_bar_line_and_pie'))
  const title = label(data.title, t('ui.title'), 120)
  if (!Array.isArray(data.labels) || !data.labels.length || data.labels.length > 40) throw new Error(t('ui.provide_between_1_and_40_categories'))
  const labels = data.labels.map(value => label(value, t('ui.category_name')))
  if (new Set(labels).size !== labels.length) throw new Error(t('ui.category_names_must_be_unique_merge_or_distinguish_the_data'))
  if (!Array.isArray(data.series) || !data.series.length || data.series.length > 6) throw new Error(t('ui.provide_between_1_and_6_data_series'))
  const series = data.series.map(value => {
    const item = object(value)
    keys(item, ['name', 'values'])
    const name = label(item.name, t('ui.series_name'))
    if (!Array.isArray(item.values) || item.values.length !== labels.length) throw new Error(t('ui.each_series_must_have_as_many_values_as_labels'))
    const values = item.values.map(value => {
      if (value === null && data.type === 'line') return null
      if (typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value) > 1e12) throw new Error(t('ui.values_must_be_finite_numbers_with_an_absolute_value_of_at_most'))
      return value
    })
    if (!values.some(value => value !== null)) throw new Error(t('ui.each_series_needs_at_least_one_valid_value'))
    return { name, values }
  })
  if (new Set(series.map(item => item.name)).size !== series.length) throw new Error(t('ui.series_names_must_be_unique'))
  if (data.type === 'pie' && (series.length !== 1 || series[0].values.some(value => value === null || value < 0) || !series[0].values.some(value => value! > 0))) {
    throw new Error(t('ui.a_pie_chart_requires_one_nonnegative_series_with_at_least_one_positive'))
  }
  return { type: data.type, title, labels, series,
    ...(data.unit !== undefined ? { unit: label(data.unit, t('ui.unit'), 24) } : {}),
    ...(data.source !== undefined ? { source: label(data.source, t('ui.data_source'), 200) } : {}),
  }
}

export function validateMermaid(source: string): string {
  checkLength(source)
  // Normalize only attribute-free break tags to text before the HTML guard.
  // Keep the stored response untouched and never enable HTML rendering.
  const text = source.trim().replace(/<br[ \t]*\/?>/gi, ' · ')
  // Fixed local theme and plain labels only. No diagram-level config, HTML,
  // resource URLs, image shapes, CSS or clickable nodes in the desktop renderer.
  if (/%%\{|^---|<[a-z/!]|@\{|(?:https?|file|data|javascript):|url\s*\(|(?:^|[\n;])\s*(?:click|classDef|linkStyle|style)\s/i.test(text)) {
    throw new Error(t('ui.use_plain_text_nodes_and_connections_without_html_links_images_custom_styles'))
  }
  const definition = text.split('\n').filter(line => !line.trim().startsWith('%%')).join('\n').trim()
  if (!/^(?:(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b|sequenceDiagram\b)/.test(definition)) {
    throw new Error(t('ui.supported_diagrams_flowchart_graph_and_sequencediagram'))
  }
  if (definition.split('\n').length > 120) throw new Error(t('ui.diagram_is_too_complex_split_it_into_smaller_diagrams'))
  return definition
}
