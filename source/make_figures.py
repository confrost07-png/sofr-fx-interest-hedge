"""
make_figures.py - 2조 MINI PROJECT 03 보고서·발표용 그래프 4종 (한글판)

입력: data/processed/monthly_analysis.csv (김동민 01_data_pipeline 결과)
출력: output/figures/timeseries.png, scatter_level.png, scatter_change.png, contribution.png
실행: 프로젝트 최상위 폴더에서  python source/make_figures.py
"""
from pathlib import Path

import matplotlib
import pandas as pd
from matplotlib import font_manager

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "output/figures"
OUT.mkdir(parents=True, exist_ok=True)

# 한글 글꼴: 설치된 것 중 첫 번째 사용 (Windows 맑은 고딕 → macOS → Colab/리눅스)
for name in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]:
    if any(f.name == name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = name
        break
plt.rcParams["axes.unicode_minus"] = False   # 빼기 기호를 하이픈(-)으로 표시
plt.rcParams["axes.spines.top"] = False
BLUE, RED, GRAY = "#1f5fa8", "#c0392b", "#9e9e9e"

m = pd.read_csv(BASE / "data/processed/monthly_analysis.csv", parse_dates=["end_date"])
x = m["end_date"]

# 1) 시계열 비교 (이중 축)
fig, ax1 = plt.subplots(figsize=(10, 4))
ax1.plot(x, m["usdkrw_end"], color=BLUE, label="원/달러 월말 환율 (왼쪽)")
ax1.set_ylabel("원 / USD")
ax2 = ax1.twinx()
ax2.plot(x, m["sofr_comp_pct"], color=RED, label="월별 복리 SOFR (오른쪽)")
ax2.set_ylabel("연 %")
ax2.spines["top"].set_visible(False)
h1, l1 = ax1.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)
ax1.set_title("원/달러 환율과 월별 복리 SOFR (2020.01~2025.12)")
fig.tight_layout()
fig.savefig(OUT / "timeseries.png", dpi=150)
plt.close(fig)

# 2) 수준값 산점도: 금리 국면별 색 구분
r_level = m["sofr_comp"].corr(m["usdkrw_end"])
groups = [
    ("저금리 (복리 SOFR 0.5% 미만)", m["sofr_comp_pct"] < 0.5, BLUE),
    ("전환기", (m["sofr_comp_pct"] >= 0.5) & (m["sofr_comp_pct"] < 3), GRAY),
    ("고금리 (복리 SOFR 3% 이상)", m["sofr_comp_pct"] >= 3, RED),
]
fig, ax = plt.subplots(figsize=(5.6, 4.4))
for label, mask, color in groups:
    d = m[mask]
    inner = f", 내부 r = {d['sofr_comp'].corr(d['usdkrw_end']):+.2f}" if len(d) > 10 else ""
    ax.scatter(d["usdkrw_end"], d["sofr_comp_pct"], s=20, color=color, label=f"{label} {len(d)}개월{inner}")
ax.set_xlabel("월말 원/달러 (원)")
ax.set_ylabel("기간 복리 SOFR (연 %)")
ax.set_title(f"수준값 산점도: r = {r_level:+.4f} (n={len(m)})")
ax.legend(loc="lower right", fontsize=8, frameon=True, framealpha=0.9)
fig.tight_layout()
fig.savefig(OUT / "scatter_level.png", dpi=150)
plt.close(fig)

# 3) 변화값 산점도
d = m[["fx_logret", "d_sofr"]].dropna()
r_change = d["d_sofr"].corr(d["fx_logret"])
fig, ax = plt.subplots(figsize=(5.6, 4.4))
ax.axhline(0, color=GRAY, lw=0.8)
ax.axvline(0, color=GRAY, lw=0.8)
ax.scatter(d["fx_logret"] * 100, d["d_sofr"] * 100, s=20, color=BLUE)
ax.set_xlabel("같은 달 환율 로그변화 (%)")
ax.set_ylabel("복리 SOFR 전월 대비 변화 (%p)")
ax.set_title(f"변화값 산점도: r = {r_change:+.4f} (n={len(d)})")
fig.tight_layout()
fig.savefig(OUT / "scatter_change.png", dpi=150)
plt.close(fig)

# 4) 월별 기여도 (만원)
v = m.dropna(subset=["d_cost"])
man = 10_000
fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(v["end_date"], v["contrib_rate"] / man, width=20, color=RED, label="금리 기여")
same = (v["contrib_rate"] > 0) == (v["contrib_fx"] > 0)
ax.bar(v["end_date"], v["contrib_fx"] / man, width=20, bottom=(v["contrib_rate"] / man).where(same, 0),
       color=BLUE, label="환율 기여")
ax.plot(v["end_date"], v["d_cost"] / man, color="black", lw=1, label="원화 이자 변화")
ax.axhline(0, color=GRAY, lw=0.8)
ax.set_ylabel("만원")
ax.set_title("월별 표준 30일 원화 이자 변화와 기여도 (금리 기여 + 환율 기여 = 이자 변화)")
ax.legend(loc="upper left", frameon=False, ncol=3)
fig.tight_layout()
fig.savefig(OUT / "contribution.png", dpi=150)
plt.close(fig)

print("저장:", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
