import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, PieChart, LineChart, HeatmapChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  VisualMapComponent,
  ToolboxComponent,
} from 'echarts/components'

export function registerECharts(): void {
  use([
    CanvasRenderer,
    BarChart,
    PieChart,
    LineChart,
    HeatmapChart,
    TitleComponent,
    TooltipComponent,
    GridComponent,
    LegendComponent,
    VisualMapComponent,
    ToolboxComponent,
  ])
}
