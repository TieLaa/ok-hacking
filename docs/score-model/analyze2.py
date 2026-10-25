# -*- coding: utf-8 -*-
import numpy as np
from dataset import ROWS

GRADES = ["D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+", "SS"]
IDX = {g: i for i, g in enumerate(GRADES)}
ATTR = ["投射", "弹跳", "运控", "体能", "防守", "意识"]

print("id  name        pos  badge score  sum   diff  k=(diff/25)  skills  yellow")
for i, (name, pos, badge, score, attrs, grades, nskill, yellow, blue) in enumerate(ROWS, 1):
    s = sum(attrs)
    d = score - s
    k = d / 25
    print(f"{i:>2}  {name:<10}{pos:<3}{badge:<3}{score:>6}{s:>6}{d:>6}{k:>10.0f}{nskill:>8}{k - nskill - 2:>7.0f}")
    assert d % 25 == 0, (i, d)

# --- grade-bonus linear model: k = sum(n_g * b_g) ---
A = np.zeros((len(ROWS), len(GRADES)))
for r, row in enumerate(ROWS):
    for g in row[5]:
        A[r, IDX[g]] += 1
k = np.array([(row[3] - sum(row[4])) / 25 for row in ROWS], dtype=float)

print("\n=== 评级加成模型 k = Σ n_g * b_g ===")
b, residuals, rank, sv = np.linalg.lstsq(A, k, rcond=None)
print("rank:", rank, "/", len(GRADES))
for g, bi in zip(GRADES, b):
    print(f"  {g:>2}: {bi:+.3f}")
pred = A @ b
print("残差:", np.round(k - pred, 1))
print("RMSE:", round(float(np.sqrt(np.mean((k - pred) ** 2))), 2))
print("R2:", round(1 - np.sum((k - pred) ** 2) / np.sum((k - k.mean()) ** 2), 3))

# --- simple grade-rank sum ---
rank = np.array([[IDX[g] for g in row[5]] for row in ROWS]).sum(axis=1)
M = np.vstack([rank, np.ones_like(rank, dtype=float)]).T
coef, *_ = np.linalg.lstsq(M, k, rcond=None)
pred2 = M @ coef
print("\n=== 评级序号求和线性模型 ===")
print("slope, intercept:", np.round(coef, 3))
print("RMSE:", round(float(np.sqrt(np.mean((k - pred2) ** 2))), 2))

# --- skill count model ---
ns = np.array([r[6] for r in ROWS], dtype=float)
M2 = np.vstack([ns, np.ones_like(ns)]).T
coef2, *_ = np.linalg.lstsq(M2, k, rcond=None)
pred3 = M2 @ coef2
print("\n=== 技能图标数线性模型 ===")
print("slope, intercept:", np.round(coef2, 3))
print("RMSE:", round(float(np.sqrt(np.mean((k - pred3) ** 2))), 2))

# --- within-character deltas ---
print("\n=== 同一角色两两对比 ===")
by = {}
for row in ROWS:
    by.setdefault(row[0], []).append(row)
for name, rows in by.items():
    if len(rows) < 2:
        continue
    print(f"-- {name}")
    for a in range(len(rows)):
        for b_ in range(a + 1, len(rows)):
            r1, r2 = rows[a], rows[b_]
            ds = sum(r1[4]) - sum(r2[4])
            dsc = r1[3] - r2[3]
            dk = (dsc - ds) / 25
            print(f"   {r1[3]}(sum{sum(r1[4])},k{(r1[3]-sum(r1[4]))//25}) vs {r2[3]}(sum{sum(r2[4])},k{(r2[3]-sum(r2[4]))//25}): dscore={dsc}, dsum={ds}, dk={dk:.0f}")
