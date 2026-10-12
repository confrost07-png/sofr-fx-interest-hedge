"""
app.py - 2조 MINI PROJECT 03 Streamlit 분석 화면 (담당: 원승연)

은행 기업금융 RM의 달러대출 상담:
복리 SOFR와 원/달러 환율이 원화 이자부담에 미친 기여를 나눠 보는 가상 분석.

실행: 프로젝트 최상위 폴더에서  streamlit run app/app.py
계산은 source/analysis_core.py(김동민)의 함수를 그대로 사용한다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from scipy import stats

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "source"))
from analysis_core import build_monthly, compute_metrics, merge_daily, read_fred_csv  # noqa: E402

import inspect  # noqa: E402

# Streamlit 1.50(맥 기본 파이썬 3.9에서 설치되는 최신)은 st.plotly_chart에 width 인자가 없어
# width="stretch"를 넘기면 화면에 노란 경고가 뜬다. 버전에 맞는 인자로 그린다.
_PLOTLY_WIDTH = "width" in inspect.signature(st.plotly_chart).parameters


def plot(fig, where=st):
    if _PLOTLY_WIDTH:
        where.plotly_chart(fig, width="stretch")
    else:
        where.plotly_chart(fig, use_container_width=True)

RAW = {"SOFRINDEX": BASE / "data/raw/sofrindex_fred.csv", "DEXKOUS": BASE / "data/raw/dexkous_fred.csv"}
BLUE, RED, GRAY = "#1f5fa8", "#c0392b", "#7f8c8d"

st.set_page_config(page_title="2조 · 복리 SOFR × 원/달러 이자부담 분석", layout="wide")


# ---------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------
@st.cache_data
def load_daily():
    sofr = read_fred_csv(RAW["SOFRINDEX"], "SOFRINDEX")
    fx = read_fred_csv(RAW["DEXKOUS"], "DEXKOUS")
    return merge_daily(sofr, fx)


@st.cache_data
def run_analysis(start, end, principal, spread):
    # 전체 기간으로 계산한 뒤 잘라 쓴다: 시작월의 변화값도 직전 달 대비로 계산 (metrics.json 보조 지표와 같은 정의)
    daily = load_daily()
    full = build_monthly(daily, "2020-01", "2025-12", principal, spread)
    m = full[(full["month"] >= start) & (full["month"] <= end)].reset_index(drop=True)
    metrics = compute_metrics(m, principal, spread)
    return m, metrics


def won(x):
    return f"{x:,.0f}원"


def man(x):
    return f"{x / 10_000:,.0f}만원"


def corr_test(xs, ys):
    """피어슨 r의 p값과 95% 신뢰구간(피셔 z). 표본이 4개 미만이면 구간을 계산하지 않는다."""
    n = len(xs)
    if n < 4:
        return None, (None, None)
    r, p = stats.pearsonr(xs, ys)
    z, se = np.arctanh(np.clip(r, -0.999999, 0.999999)), 1 / np.sqrt(n - 3)
    return float(p), (float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se)))


def p_text(p):
    return "-" if p is None else ("< 0.001" if p < 0.001 else f"{p:.3f}")


def hyphen_axis(fig, axis, lo, hi, n=6):
    """plotly는 음수 눈금을 긴 빼기 기호(U+2212)로 그리므로, 눈금을 직접 만들어 하이픈(-)으로 표시한다."""
    span = (hi - lo) or abs(hi) or 1.0
    raw = span / n
    mag = 10 ** np.floor(np.log10(raw))
    step = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    ticks = np.arange(np.floor(lo / step) * step, hi + step * 0.5, step)
    dec = 0                                  # 눈금 간격(2.5, 0.25 등)을 그대로 보여줄 소수 자릿수
    while dec < 6 and abs(round(step * 10 ** dec) - step * 10 ** dec) > 1e-9:
        dec += 1
    text = [f"{t:,.{dec}f}" for t in ticks]
    text = ["0" if float(t.replace(",", "")) == 0 else t for t in text]
    getattr(fig, f"update_{axis}axes")(tickvals=ticks, ticktext=text)


daily = load_daily()
months = pd.period_range("2020-01", "2025-12", freq="M").astype(str).tolist()

# ---------------------------------------------------------------
# 사이드바: 조건 선택
# ---------------------------------------------------------------
with st.sidebar:
    st.header("분석 조건")
    start, end = st.select_slider("분석 기간 (시작월 ~ 종료월)", options=months, value=("2020-01", "2025-12"))
    principal = st.number_input("가상 원금 (USD)", min_value=100_000, max_value=50_000_000,
                                value=1_000_000, step=100_000, format="%d")
    spread_pct = st.number_input("고정 가산금리 (연 %p)", min_value=0.0, max_value=10.0, value=2.0, step=0.25)
    corr_mode = st.radio("상관 기준", ["수준값", "변화값"], horizontal=True,
                         help="수준값: 기간 복리 SOFR와 월말 환율 / 변화값: SOFR 변화와 같은 달 환율 로그변화")
    st.caption("기간·원금·가산금리를 바꾸면 모든 수치가 다시 계산됩니다.")

spread = spread_pct / 100
n_periods = pd.Period(end, "M").ordinal - pd.Period(start, "M").ordinal + 1
if n_periods < 3:
    st.warning("상관계수를 계산하려면 분석 기간을 3개월 이상으로 골라 주세요.")
    st.stop()

m, metrics = run_analysis(start, end, int(principal), spread)
valid = m.dropna(subset=["d_cost"])
x = pd.to_datetime(m["end_date"])

# ---------------------------------------------------------------
# 헤더
# ---------------------------------------------------------------
st.title("복리 SOFR × 원/달러 원화 이자부담 분석")
st.caption(f"2조 MINI PROJECT 03 · 은행 기업금융 RM 달러대출 상담용 가상 분석 · "
           f"{start} ~ {end} · 원금 USD {principal:,.0f} · 가산금리 연 {spread_pct:g}%p")

# ---------------------------------------------------------------
# 요약 카드
# ---------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"수준값 상관 (n={metrics['n_level']})", f"{metrics['corr_level']:+.4f}")
c2.metric(f"변화값 상관 (n={metrics['n_change']})", f"{metrics['corr_change']:+.4f}")
c3.metric(f"환율 기여 우세 월 (금리 우세 {metrics['rate_dominant_months']})",
          f"{metrics['fx_dominant_months']} / {metrics['counts']['changes']}")
c4.metric("기여 절댓값 합 금리 / 환율",
          f"{metrics['sum_abs_contrib_rate_krw'] / 1e4:,.0f} / {metrics['sum_abs_contrib_fx_krw'] / 1e4:,.0f}만원",
          help="방향을 뺀 월별 변동 규모의 합 (누적 비용 아님)")

tab_ts, tab_corr, tab_rm, tab_note = st.tabs(["시계열", "상관분석", "RM 상담용 결과", "자료·해석 안내"])

# ---------------------------------------------------------------
# 시계열
# ---------------------------------------------------------------
with tab_ts:
    left, right = st.columns(2)
    with left:
        f1 = go.Figure(go.Scatter(x=x, y=m["usdkrw_end"], line=dict(color=BLUE), name="원/달러"))
        f1.update_layout(title="원/달러 월말 환율", yaxis_title="원 / USD", height=320, margin=dict(t=50, b=30))
        plot(f1)
    with right:
        f2 = go.Figure(go.Scatter(x=x, y=m["sofr_comp_pct"], line=dict(color=RED), name="복리 SOFR"))
        f2.update_layout(title="월별 기간 복리 SOFR (연율)", yaxis_title="%", height=320, margin=dict(t=50, b=30))
        plot(f2)

    f3 = make_subplots(specs=[[{"secondary_y": True}]])
    f3.add_trace(go.Scatter(x=x, y=m["usdkrw_end"], name="원/달러 (왼쪽)", line=dict(color=BLUE)), secondary_y=False)
    f3.add_trace(go.Scatter(x=x, y=m["sofr_comp_pct"], name="복리 SOFR % (오른쪽)", line=dict(color=RED)),
                 secondary_y=True)
    f3.update_yaxes(title_text="원 / USD", secondary_y=False)
    f3.update_yaxes(title_text="%", secondary_y=True)
    f3.update_layout(title="두 변수 비교 (이중 축)", height=380, legend=dict(orientation="h", y=1.12),
                     margin=dict(t=70, b=30))
    plot(f3)
    st.caption("두 선은 축 단위가 다르므로 높이를 직접 비교하지 않습니다. 같은 시기에 함께 오르내렸는지만 봅니다.")

# ---------------------------------------------------------------
# 상관분석
# ---------------------------------------------------------------
with tab_corr:
    if corr_mode == "수준값":
        d = m[["usdkrw_end", "sofr_comp_pct", "month"]].dropna()
        xs, ys = d["usdkrw_end"], d["sofr_comp_pct"]
        xt, yt = "월말 원/달러 (원)", "기간 복리 SOFR (%)"
        r, n = metrics["corr_level"], metrics["n_level"]
    else:
        d = m[["fx_logret", "d_sofr", "month"]].dropna()
        xs, ys = d["fx_logret"] * 100, d["d_sofr"] * 100
        xt, yt = "환율 로그변화 (%)", "복리 SOFR 변화 (%p)"
        r, n = metrics["corr_change"], metrics["n_change"]

    p, (lo95, hi95) = corr_test(xs.values, ys.values)
    a, b = st.columns([1, 3])
    a.metric(f"{corr_mode} 상관계수", f"{r:+.4f}")
    a.metric("표본 수", f"{n}개월")
    a.metric("유의확률 (p값)", p_text(p),
             help="관계가 없는데 우연히 이 정도 상관이 나올 확률. 0.05보다 작으면 통계적으로 유의하다고 본다. "
                  "작다고 관계가 강하거나 원인이라는 뜻은 아니다.")
    a.metric("95% 신뢰구간", "-" if lo95 is None else f"{lo95:+.2f} ~ {hi95:+.2f}")
    if corr_mode == "수준값":
        a.warning("수준값은 두 변수 모두 추세가 있어 p값이 실제보다 작게 나옵니다(가짜 상관 주의). "
                  "결론은 변화값과 기여도로 봅니다.")
    else:
        a.caption("수준값 상관이 높아도 월별 변화가 같이 움직였다는 뜻은 아닙니다. 두 기준을 함께 보세요.")
    fs = go.Figure(go.Scatter(x=xs, y=ys, mode="markers", text=d["month"],
                              marker=dict(color=BLUE, size=8, opacity=0.75),
                              hovertemplate="%{text}<br>x=%{x:.2f}<br>y=%{y:.2f}<extra></extra>"))
    fs.update_layout(title=f"{corr_mode} 산점도 · r = {r:+.4f} (n={n}, p {p_text(p) if p_text(p).startswith('<') else '= ' + p_text(p)})",
                     xaxis_title=xt, yaxis_title=yt,
                     height=420, margin=dict(t=50, b=40))
    hyphen_axis(fs, "x", xs.min(), xs.max())
    hyphen_axis(fs, "y", ys.min(), ys.max())
    plot(fs, b)

# ---------------------------------------------------------------
# RM 상담용 결과
# ---------------------------------------------------------------
with tab_rm:
    first, last = m.iloc[0], m.iloc[-1]
    total = valid["d_cost"].sum()
    rate_sum, fx_sum = valid["contrib_rate"].sum(), valid["contrib_fx"].sum()
    k1, k2, k3 = st.columns(3)
    # 기간 중 변화(total)에는 시작월의 '전월 대비 변화'도 들어 있으므로, 시작 카드는 그 전월 이자로 표시
    base_cost = last["cost_30d_krw"] - total
    base_month = (pd.Period(first["month"], "M") - 1).strftime("%Y-%m") if pd.notna(first["d_cost"]) else first["month"]
    k1.metric(f"표준 30일 원화 이자 ({base_month})", won(base_cost))
    k2.metric(f"표준 30일 원화 이자 ({last['month']})", won(last["cost_30d_krw"]))
    k3.metric("기간 중 변화", man(total), help=f"금리 {man(rate_sum)} · 환율 {man(fx_sum)}")
    st.caption(f"기간 중 변화 {man(total)} = 금리 기여 {man(rate_sum)} + 환율 기여 {man(fx_sum)} " + (f"(금리 비중 {rate_sum / total:.1%})" if rate_sum * total >= 0 and fx_sum * total >= 0 else "(두 요인이 반대 방향)") if total else "")

    dx = pd.to_datetime(valid["end_date"])
    cr, cf, dc = valid["contrib_rate"] / 1e4, valid["contrib_fx"] / 1e4, valid["d_cost"] / 1e4
    hov = "%{x|%Y-%m}<br>%{fullData.name} %{y:,.1f}만원<extra></extra>"
    fc = go.Figure()
    fc.add_trace(go.Bar(x=dx, y=cr, name="금리 기여", marker_color=RED, hovertemplate=hov))
    fc.add_trace(go.Bar(x=dx, y=cf, name="환율 기여", marker_color=BLUE, hovertemplate=hov))
    fc.add_trace(go.Scatter(x=dx, y=dc, name="원화 이자 변화", line=dict(color="black", width=1), hovertemplate=hov))
    fc.update_layout(barmode="relative", title="월별 원화 이자 변화와 기여도 (대칭분해)", yaxis_title="만원",
                     height=400, legend=dict(orientation="h", y=1.12), margin=dict(t=70, b=30))
    lo = min((cr.clip(upper=0) + cf.clip(upper=0)).min(), dc.min())
    hi = max((cr.clip(lower=0) + cf.clip(lower=0)).max(), dc.max())
    hyphen_axis(fc, "y", lo, hi)
    plot(fc)
    st.caption("금리 기여 + 환율 기여 = 그달 원화 이자 변화 (교차항을 절반씩 나눈 대칭분해)")

    st.subheader("기여가 컸던 달")
    top = valid.assign(abs_change=valid["d_cost"].abs()).nlargest(5, "abs_change")
    show = pd.DataFrame({
        "월": top["month"],
        "이자 변화": top["d_cost"].map(man),
        "금리 기여": top["contrib_rate"].map(man),
        "환율 기여": top["contrib_fx"].map(man),
        "더 큰 쪽": top["dominant"].map({"RATE": "금리", "FX": "환율", "TIE": "동률"}),
    })
    st.dataframe(show, hide_index=True, width="stretch")

    pick = st.selectbox("상담할 달 고르기", valid["month"].tolist()[::-1])
    row = valid.set_index("month").loc[pick]
    big = "금리" if abs(row["contrib_rate"]) >= abs(row["contrib_fx"]) else "환율"
    st.info(f"**{pick}** 원화 이자가 {man(row['d_cost'])} 변했습니다. "
            f"금리(SOFR) 요인 {man(row['contrib_rate'])}, 환율 요인 {man(row['contrib_fx'])}으로 "
            f"이번 달은 **{big}** 영향이 더 컸습니다.")

# ---------------------------------------------------------------
# 자료·해석 안내
# ---------------------------------------------------------------
def robustness_text():
    """output/robustness.json(강건성 점검, 전체 기간 고정)의 핵심 숫자. 파일이 없으면 안내만 한다."""
    p = BASE / "output/robustness.json"
    if not p.exists():
        return "- `python source/robustness_check.py`를 실행하면 강건성 점검 결과가 여기에 표시됩니다."
    R = json.loads(p.read_text(encoding="utf-8"))
    c = {x["name"]: x for x in R["correlations"]}
    ch, l3, usd, dec = c["변화값 전체"], R["regressions"]["L3"]["coef"]["sofr"], R["regressions"]["dollar_only"], R["decomposition"]
    shares = [dec[k]["rate_share"] for k in ("symmetric", "rate_first", "fx_first")]
    starts = [v["rate_share"] for v in dec["start_month"].values()]
    return (f"- 변화값 상관 r = {ch['r']:+.2f} (p = {ch['p']:.2f}, 95% 신뢰구간 {ch['ci95'][0]:+.2f} ~ {ch['ci95'][1]:+.2f}, "
            f"BH 보정 q = {ch['q_bh']:.2f}): 순위상관·2020-03 제외·통제변수에서도 같음\n"
            f"- 금리 국면을 고정하면 SOFR 1%p당 환율 {l3['b']:+.0f}원 (p = {l3['p_hac']:.2f}): 수준값 +0.80은 국면 차이\n"
            f"- 월별 환율 변화의 {usd['r2']:.0%}는 글로벌 달러지수 변화로 설명됨 (SOFR 변화는 "
            f"{R['regressions']['C1']['diag']['r2']:.1%})\n"
            f"- 금리 몫: 분해 방식에 따라 {min(shares):.1%} ~ {max(shares):.1%}, 시작 달에 따라 {min(starts):.0%} ~ {max(starts):.0%}\n"
            f"- 검정 {R['multiple_testing']['m']}개에 벤자미니-호크버그 보정: 유의 {R['multiple_testing']['raw_p_lt_005']}개 → "
            f"{R['multiple_testing']['bh_significant']}개 (자세한 표는 source/03_robustness_check.ipynb)")


with tab_note:
    st.markdown(
        f"""
**자료 출처**
- SOFR Index (SOFRINDEX): 뉴욕 연준, FRED 경유, 일별 지수 · https://fred.stlouisfed.org/series/SOFRINDEX
- 원/달러 (DEXKOUS): 미 연준 H.10, FRED 경유, 1달러당 원, 일별 · https://fred.stlouisfed.org/series/DEXKOUS

**결합·계산 기준**
- 두 자료 모두 값이 있는 공통 날짜만 사용하고, 휴일 결측은 채우지 않습니다.
- 매월 마지막 공통 관측일을 월말 끝점으로 씁니다 (시작점은 시작월 직전 달 말).
- 기간 복리 SOFR = (Index 말 ÷ Index 초 - 1) × 360 ÷ 실제 경과일수
- 표준 30일 원화 이자 = 원금 × (복리 SOFR + 가산금리) ÷ 12 × 월말 환율
- 현재 선택: 계산 기간 {metrics['counts']['periods']}개, 변화 비교 {metrics['counts']['changes']}개,
  기여도 분해 최대 오차 {metrics['max_decomp_residual']:.1e}원

**해석 한계**
- 실제 은행 청구액, 손익, 차주 신용위험을 측정하지 않는 가상 분석입니다.
- 상관관계는 인과관계가 아닙니다. 환율 예측 근거로 쓰지 않습니다.
- 표준 30일 이자는 기간 길이를 맞춘 비교용 지표이며, 실제 계약의 재설정일·일수 계산·헤지를 반영하지 않습니다.
- 기간을 바꾸면 상관계수의 부호까지 달라질 수 있습니다. 수준값 상관은 추세 때문에 p값이 과장되므로 참고로만 봅니다.
- 투자 권유가 아닙니다.

**강건성 점검 (전체 기간 2020-01 ~ 2025-12 고정, output/robustness.json)**
{robustness_text()}
"""
    )
