import { init, use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { ChartSpec } from './visualizationSpec'

use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, SVGRenderer])
const colors = ['#3b9b85', '#6c8dc5', '#d5a354', '#a08ab9', '#cb8490', '#7ca5b4']
const font = 'Microsoft YaHei, PingFang SC, Arial, sans-serif'

export function renderChart(spec: ChartSpec): string {
  const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 880, height: 420 })
  try {
    chart.setOption({ animation: false, color: colors, textStyle: { fontFamily: font, fontSize: 13, color: '#425f68' },
      legend: { show: spec.type !== 'pie', top: 10, selectedMode: false, icon: 'roundRect', itemWidth: 12, itemHeight: 8,
        itemGap: 24, textStyle: { color: '#647c83', fontSize: 13 }, data: spec.series.map(item => item.name) },
      ...(spec.type !== 'pie' ? {
        grid: { left: 42, right: 32, top: 64, bottom: 48, containLabel: true },
        xAxis: { type: 'category', data: spec.labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#d8e4e4' } },
          axisLabel: { color: '#647c83', margin: 16, rotate: spec.labels.length > 8 ? 35 : 0, width: 95, overflow: 'truncate' } },
        yAxis: { type: 'value', name: spec.unit || '', nameTextStyle: { color: '#82959a', padding: [0, 10, 6, 0] },
          axisLabel: { color: '#82959a', margin: 14 }, splitLine: { lineStyle: { color: '#e7eeee', type: 'dashed' } } },
      } : {}),
      series: spec.type === 'pie' ? [{ type: 'pie', silent: true, radius: ['0%', '66%'], center: ['50%', '50%'],
        itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 5 },
        labelLine: { length: 18, length2: 14, lineStyle: { color: '#b6c8c9' } },
        label: { formatter: '{b}\n{c} ({d}%)', lineHeight: 20, width: 155, overflow: 'truncate', color: '#425f68' },
        data: spec.labels.map((name, index) => ({ name, value: spec.series[0].values[index] })) }] : spec.series.map(item => ({
          name: item.name, type: spec.type, data: item.values, silent: true, connectNulls: false,
          smooth: false, barMaxWidth: 48, barGap: '35%', symbol: 'circle', symbolSize: 8,
          ...(spec.type === 'bar' ? { itemStyle: { borderRadius: 4 } }
            : { lineStyle: { width: 3 }, itemStyle: { borderWidth: 2, borderColor: '#fff' } }),
          label: { show: spec.labels.length <= 10 && spec.series.length <= 2, position: 'top', distance: 10, color: '#425f68', fontWeight: 600 },
        })),
    })
    return chart.renderToSVGString()
  } finally { chart.dispose() }
}
