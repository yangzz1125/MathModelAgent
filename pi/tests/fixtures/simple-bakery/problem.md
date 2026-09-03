# Micro Bakery Production Study

A small bakery produces two continuous products each day:

| Product | Flour per unit | Labor per unit | Profit per unit |
|---|---:|---:|---:|
| Bread, x | 2 | 1 | 30 |
| Cake, y | 1 | 2 | 40 |

Both production quantities are nonnegative real numbers. The baseline labor capacity is 80 units. The accompanying `resource_scenarios.csv` contains flour-capacity scenarios.

## Question 1

For flour capacity 100 and labor capacity 80, formulate and solve the profit-maximization model. Independently verify feasibility and optimality by enumerating all vertices of the two-dimensional feasible polygon.

Report metrics named exactly `bread_units`, `cake_units`, and `max_profit`.

## Question 2

Increase flour capacity to 110 while labor remains 80. Find the new optimum and compare it with Question 1. Report metrics named exactly `bread_units`, `cake_units`, `max_profit`, and `profit_increase`.

Question 2 depends on the accepted result of Question 1.

## Question 3

For every row of `resource_scenarios.csv`, compute the optimal bread quantity, cake quantity, and maximum profit. Save `results/q3/sensitivity.csv` with columns `flour_capacity,labor_capacity,bread_units,cake_units,max_profit`. Generate one readable data chart under `figures/q3/` showing maximum profit versus flour capacity, and explain the marginal value pattern.

Question 3 depends on the accepted results of Questions 1 and 2.

## Deliverable

Produce a concise English MCM-style paper. All calculations must be reproducible with Python standard-library code; this tiny problem does not require SciPy or a long optimization run.
