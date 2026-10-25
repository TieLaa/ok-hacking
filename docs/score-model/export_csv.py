# -*- coding: utf-8 -*-
import csv
from dataset import ROWS

out = []
for i, (name, pos, badge, score, attrs, grades, nskill, yellow, blue) in enumerate(ROWS, 1):
    total = sum(attrs)
    k = (score - total) // 25
    skill_points = 75 + 25 + 50 * yellow + 25 * blue   # 1 彩 + 1 绿 + 黄 + 蓝
    assert skill_points == 25 * k, (name, score, skill_points, k)
    assert nskill == 2 + yellow + blue, (name, nskill, yellow, blue)
    out.append({
        "id": i, "name": name, "position": pos, "badge": badge, "score": score,
        "shoot": attrs[0], "jump": attrs[1], "handle": attrs[2],
        "stamina": attrs[3], "defense": attrs[4], "awareness": attrs[5],
        "attr_sum": total, "k": k,
        "color": 1, "green": 1, "yellow": yellow, "blue": blue,
        "skills": nskill, "skill_points": skill_points, "pred": total + skill_points,
    })

with open("data.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)

print(f"{'id':>2} {'name':<10} {'score':>5} {'sum':>5} {'C':>1} {'G':>1} {'Y':>1} {'B':>1} "
      f"{'N':>2} {'k':>2} {'skill':>5}  pred_ok")
for r in out:
    print(f"{r['id']:>2} {r['name']:<10} {r['score']:>5} {r['attr_sum']:>5} "
          f"{r['color']:>1} {r['green']:>1} {r['yellow']:>1} {r['blue']:>1} "
          f"{r['skills']:>2} {r['k']:>2} {r['skill_points']:>5}  {r['pred'] == r['score']}")
