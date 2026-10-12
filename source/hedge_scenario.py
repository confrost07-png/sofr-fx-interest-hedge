"""hedge_scenario.py - 헤지 시나리오 비교 (담당: 조진영)

가상 차주(원금 USD 1,000,000, 가산금리 연 2%p)의 표준 30일 원화 이자가
2020-01 → 2025-12 동안 늘어난 금액을, 처음 시점에 위험을 고정했다면 얼마나 줄일 수 있었는지 비교한다.

  - 헤지 없음 : 금리·환율 모두 그대로
  - IRS       : 금리를 2020-01 수준으로 고정 (환율 위험은 그대로)
  - 선물환    : 환율을 2020-01 수준으로 고정 (금리 위험은 그대로)
  - 둘 다     : 금리·환율 모두 2020-01 수준으로 고정

※ 고정 수준 = 시작 달의 실제 값으로 둔 단순화 비교. 스왑 금리·선물환 포인트·헤지 비용은 반영하지 않음.
실행: 프로젝트 최상위 폴더에서  python source/hedge_scenario.py
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRINCIPAL, SPREAD = 1_000_000, 0.02


def cost_30d(rate, fx):
    """표준 30일 원화 이자 = 원금 × (복리 SOFR + 가산금리) ÷ 12 × 월말 환율"""
    return PRINCIPAL * (rate + SPREAD) / 12 * fx


def main():
    m = pd.read_csv(ROOT / "data" / "processed" / "monthly_analysis.csv", encoding="utf-8-sig")
    first, last = m.iloc[0], m.iloc[-1]
    r0, f0 = first.sofr_comp, first.usdkrw_end
    r1, f1 = last.sofr_comp, last.usdkrw_end

    base = cost_30d(r0, f0)
    scenarios = {
        "헤지 없음": cost_30d(r1, f1),
        "IRS (금리 고정)": cost_30d(r0, f1),
        "선물환 (환율 고정)": cost_30d(r1, f0),
        "IRS + 선물환": cost_30d(r0, f0),
    }
    unhedged_increase = scenarios["헤지 없음"] - base

    rows = []
    for name, cost in scenarios.items():
        increase = cost - base
        saved = unhedged_increase - increase
        rows.append({
            "시나리오": name,
            "2025-12 이자(만원)": round(cost / 1e4),
            "늘어난 이자(만원)": round(increase / 1e4),
            "줄인 금액(만원)": round(saved / 1e4),
            "줄인 비율(%)": round(saved / unhedged_increase * 100, 1),
        })
    out = pd.DataFrame(rows)
    print(f"기준 2020-01: SOFR {r0*100:.2f}% · 원/달러 {f0:,.1f}원 · 이자 {base/1e4:,.0f}만원")
    print(f"비교 2025-12: SOFR {r1*100:.2f}% · 원/달러 {f1:,.1f}원\n")
    print(out.to_string(index=False))
    out.to_csv(ROOT / "output" / "hedge_scenario.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
