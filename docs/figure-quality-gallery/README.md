# Figure Quality Gallery

这些图用于验证绘图能力和视觉规则，不是建模结论，也不能作为论文证据。

## 基础图

总览：[`basic_gallery.png`](basic_gallery.png)

灰度总览：[`basic_gallery_grayscale.png`](basic_gallery_grayscale.png)

| 图型 | PNG | 矢量文件 |
| --- | --- | --- |
| 多系列折线 | [`multi_line.png`](multi_line.png) | [`PDF`](multi_line.pdf) · [`SVG`](multi_line.svg) |
| 分组柱状 | [`grouped_bar.png`](grouped_bar.png) | [`PDF`](grouped_bar.pdf) · [`SVG`](grouped_bar.svg) |
| 横向排名 | [`horizontal_ranking.png`](horizontal_ranking.png) | [`PDF`](horizontal_ranking.pdf) · [`SVG`](horizontal_ranking.svg) |
| 箱线与原始点 | [`boxplot.png`](boxplot.png) | [`PDF`](boxplot.pdf) · [`SVG`](boxplot.svg) |
| 气泡散点 | [`bubble_scatter.png`](bubble_scatter.png) | [`PDF`](bubble_scatter.pdf) · [`SVG`](bubble_scatter.svg) |
| 堆叠面积 | [`stacked_area.png`](stacked_area.png) | [`PDF`](stacked_area.pdf) · [`SVG`](stacked_area.svg) |

生成脚本：[`render_basic_gallery.py`](render_basic_gallery.py)。数据为确定性视觉回归 fixture，仅用于测试布局、中文字体、矢量导出和灰度可辨识性。

## 既有图

- [`gallery.png`](gallery.png)：此前八类基础/建模图总览。
- [`bakery_value_showcase.png`](bakery_value_showcase.png)：使用冻结 bakery 敏感性数据的单面板示例。
- [`bakery_decision_dashboard.png`](bakery_decision_dashboard.png)：使用同一冻结数据的三面板决策叙事图。
- [`bakery_decision_dashboard_grayscale.png`](bakery_decision_dashboard_grayscale.png)：三面板图灰度检查。
