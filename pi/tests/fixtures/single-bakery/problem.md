# Parametric Bakery Capacity Study

A bakery produces continuous quantities of bread, `x`, and cake, `y` each day.

| Product | Flour per unit | Labor per unit | Profit per unit |
|---|---:|---:|---:|
| Bread | 2 | 1 | 30 |
| Cake | 1 | 2 | 40 |

Labor capacity is fixed at 80 units. Flour capacity `F` may vary over the interval `0 <= F <= 180`. Production quantities are nonnegative real numbers.

## Question 1

Build and solve a parametric profit-maximization model that provides all of the following in one coherent analysis:

1. At the baseline flour capacity `F = 100`, report the optimal bread quantity, cake quantity, and maximum profit using metric names exactly `bread_units`, `cake_units`, and `max_profit`.
2. Derive the complete piecewise formula for the optimal value `V(F)` on `0 <= F <= 180`, including every capacity breakpoint and the optimal production regime in each interval. Report the breakpoints using metric names exactly `first_breakpoint` and `second_breakpoint`.
3. Explain the economic meaning of the flour and labor shadow prices in every differentiable regime and the one-sided marginal values at each breakpoint.
4. Independently validate the solution by exhaustively enumerating all feasible polygon vertices at representative capacities on both sides of every breakpoint. Also give a primal-dual certificate at `F = 100`; the vertex audit must not reuse the primary candidate-generation implementation.
5. Save `results/q1/sensitivity.csv` with columns exactly `flour_capacity,bread_units,cake_units,max_profit` for `F = 0, 20, 40, 70, 100, 130, 160, 180`.
6. Generate one readable data figure under `figures/q1/` showing `V(F)`, with both breakpoints clearly identified and no clipped labels.
7. Discuss numerical tolerance, behavior exactly at the breakpoints, modeling limitations, and how the conclusion would change if production had to be integral.

所有计算必须仅使用 Python 标准库复现，不使用 SciPy 或外部优化器。最终生成一篇科学论证完整、内容充实但不靠空话凑页数的中文全国大学生数学建模竞赛风格论文。摘要必须概括方法、分段结论、基准数值和验证结果；正文应包含问题重述、假设、符号说明、模型建立、解析推导、算法、结果、独立验证、灵敏度分析、模型评价和结论。
