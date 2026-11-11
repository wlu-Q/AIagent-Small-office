import { t } from '../../i18n'
import { parseChart, validateMermaid, type VisualizationLanguage } from './visualizationSpec'

const font = 'Microsoft YaHei, PingFang SC, Arial, sans-serif'
let diagramId = 0
let mermaidReady: Promise<typeof import('mermaid')['default']> | undefined

function loadMermaid() {
  if (!mermaidReady) mermaidReady = import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
      maxTextSize: 12000, maxEdges: 100, theme: 'base', fontFamily: font, htmlLabels: false,
      themeVariables: { primaryColor: '#e9f4ef', primaryTextColor: '#244b43', primaryBorderColor: '#9bc8b6',
        lineColor: '#87a5a0', secondaryColor: '#edf2fa', tertiaryColor: '#fbf4e8', fontFamily: font,
        fontSize: '15px', clusterBkg: '#f6f9fc', clusterBorder: '#d7e3ed', edgeLabelBackground: '#fff',
        actorBkg: '#e9f4ef', actorBorder: '#9bc8b6', actorTextColor: '#244b43', actorLineColor: '#d2e1df',
        signalColor: '#6c8e89', signalTextColor: '#39585b', labelBoxBkgColor: '#edf2fa', labelBoxBorderColor: '#c8d6e8',
        noteBkgColor: '#fbf4e8', noteBorderColor: '#e6d6b3', noteTextColor: '#6b5734',
        activationBkgColor: '#e1ede9', activationBorderColor: '#aac9bd' },
      themeCSS: '.node rect { rx: 9px; ry: 9px; } .node rect, .node polygon, .node circle { stroke-width: 1.25px; } .flowchart-link { stroke-width: 1.5px; }',
      flowchart: { htmlLabels: false, useMaxWidth: false, curve: 'linear', nodeSpacing: 36, rankSpacing: 48, padding: 18 },
      sequence: { useMaxWidth: false, actorMargin: 60, messageMargin: 40 },
    })
    return mermaid
  }).catch(error => { mermaidReady = undefined; throw error })
  return mermaidReady
}

export async function renderVisualization(language: VisualizationLanguage, source: string) {
  let svg: string
  let title: string
  let caption = ''
  let description: string
  if (language === 'mermaid') {
    const definition = validateMermaid(source)
    const mermaid = await loadMermaid()
    const container = document.createElement('div')
    container.style.cssText = 'position:absolute;left:-100000px;top:0;visibility:hidden;pointer-events:none;'
    container.setAttribute('aria-hidden', 'true')
    document.body.appendChild(container)
    try {
      svg = (await mermaid.render(`office-diagram-${++diagramId}`, definition, container)).svg
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      if (/parse error|syntax error/i.test(message)) {
        const line = message.match(/line\s+(\d+)/i)?.[1]
        throw new Error(t('chart.syntax_error', { line: line ? t('chart.near_line', { line }) : '' }))
      }
      throw cause
    } finally { container.remove() }
    title = definition.startsWith('sequenceDiagram') ? t('ui.sequence_diagram') : t('ui.flow_and_architecture_diagram')
    description = t('ui.value_zoom_in_to_view_nodes_and_relationships', { value1: (title) })
  } else {
    const spec = parseChart(source)
    svg = (await import('./renderChart')).renderChart(spec)
    title = spec.title
    caption = [spec.unit && t('ui.unit_value', { value1: (spec.unit) }), spec.source && t('ui.source_value', { value1: (spec.source) })].filter(Boolean).join(' · ')
    description = spec.series.map(item => `${item.name}：${spec.labels.map((name, i) => `${name} ${item.values[i] ?? t('ui.missing')}${spec.unit || ''}`).join(', ')}`).join('; ')
  }
  // SVG is displayed as an image, not inserted into the application DOM. The
  // generated document cannot access DesktopBridge or execute event handlers.
  return { url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, title, caption, description }
}
