"""
analysis_core.py
2조 MINI PROJECT 03 - 복리 SOFR · 원/달러 환율 계산 모듈 (담당: 김동민)

Streamlit 앱(원승연)도 이 파일의 함수를 import해서
기간·원금·가산금리를 바꿀 때 같은 방식으로 재계산한다.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------
# 1) 원본 읽기 / 정제
# ---------------------------------------------------------------
def read_fred_csv(path, series_id):
    """FRED CSV를 [date, 값] 형태로 읽는다.
    - 날짜 컬럼 이름이 'observation_date' 또는 'DATE'인 두 형식을 모두 지원
    - 결측('.' 또는 빈칸)은 NaN으로 변환 (전일값으로 채우지 않음)
    """
    df = pd.read_csv(path)
    date_col = next((c for c in ["observation_date", "DATE"] if c in df.columns), df.columns[0])
    val_col = series_id if series_id in df.columns else df.columns[1]
    return pd.DataFrame({
        "date": pd.to_datetime(df[date_col]),
        series_id.lower(): pd.to_numeric(df[val_col], errors="coerce"),
    })


def merge_daily(sofr, fx):
    """두 시계열 모두 값이 있는 공통 날짜만 남긴다 (inner join)."""
    s = sofr.dropna()
    f = fx.dropna()
    daily = s.merge(f, on="date", how="inner").sort_values("date").reset_index(drop=True)
    return daily  # columns: date, sofrindex, dexkous


# ---------------------------------------------------------------
# 2) 월말 끝점 선택
# ---------------------------------------------------------------
def month_end_points(daily, start_month, end_month):
    """시작월 직전 달 ~ 종료월의 '매월 마지막 공통 관측일'을 뽑는다.
    예) 2020-01 ~ 2025-12 → 2019-12 말 포함 73개 끝점
    """
    d = daily.copy()
    d["ym"] = d["date"].dt.to_period("M")
    sp, ep = pd.Period(start_month, "M"), pd.Period(end_month, "M")
    d = d[(d["ym"] >= sp - 1) & (d["ym"] <= ep)]
    pts = d.sort_values("date").groupby("ym").tail(1).reset_index(drop=True)

    if pts.empty or pts["ym"].iloc[0] != sp - 1:
        raise ValueError(f"{sp - 1} 월말 시작점이 데이터에 없습니다.")
    expected = pd.period_range(sp - 1, ep, freq="M")
    missing = sorted(set(expected) - set(pts["ym"]))
    if missing:
        raise ValueError(f"관측값이 없는 달이 있습니다: {missing}")
    return pts


# ---------------------------------------------------------------
# 3) 월별 분석 테이블 (복리 SOFR, 환율 변화, 이자액, 기여도)
# ---------------------------------------------------------------
def build_monthly(daily, start_month="2020-01", end_month="2025-12",
                  principal=1_000_000, spread=0.02):
    pts = month_end_points(daily, start_month, end_month)
    prev, cur = pts.iloc[:-1].reset_index(drop=True), pts.iloc[1:].reset_index(drop=True)

    m = pd.DataFrame({
        "month": cur["ym"].astype(str),
        "start_date": prev["date"].dt.date,
        "end_date": cur["date"].dt.date,
        "sofr_index_start": prev["sofrindex"],
        "sofr_index_end": cur["sofrindex"],
        "usdkrw_start": prev["dexkous"],
        "usdkrw_end": cur["dexkous"],
    })
    m["days"] = (cur["date"] - prev["date"]).dt.days

    # 기간 복리 SOFR (연율, 소수) = (Index말/Index초 - 1) × 360 / 실제 경과일수
    growth = m["sofr_index_end"] / m["sofr_index_start"] - 1
    m["sofr_comp"] = growth * 360 / m["days"]
    m["sofr_comp_pct"] = m["sofr_comp"] * 100

    # 환율 로그변화율
    m["fx_logret"] = np.log(m["usdkrw_end"] / m["usdkrw_start"])

    # 표준 30일 원화 이자액 C
    r, F = m["sofr_comp"], m["usdkrw_end"]
    m["cost_30d_krw"] = principal * (r + spread) / 12 * F

    # 월간 변화 및 대칭분해 기여도
    r0, F0 = r.shift(1), F.shift(1)
    m["d_sofr"] = r - r0
    m["d_cost"] = m["cost_30d_krw"].diff()
    m["contrib_rate"] = principal / 12 * (r - r0) * (F + F0) / 2
    m["contrib_fx"] = principal / 12 * (F - F0) * ((r + spread) + (r0 + spread)) / 2
    m["decomp_residual"] = m["d_cost"] - (m["contrib_rate"] + m["contrib_fx"])

    a_r, a_f = m["contrib_rate"].abs(), m["contrib_fx"].abs()
    m["dominant"] = np.select([a_f > a_r, a_r > a_f], ["FX", "RATE"], default="TIE")
    m.loc[m["d_cost"].isna(), "dominant"] = None

    # 참고: 실제 경과일수 반영 가상 발생이자 (표준 30일 비교액과 별개)
    m["interest_actual_usd"] = principal * (growth + spread * m["days"] / 360)
    m["interest_actual_krw"] = m["interest_actual_usd"] * F
    return m


# ---------------------------------------------------------------
# 4) 지표 (상관계수, 우세 월, 기여 절댓값 합)
# ---------------------------------------------------------------
def compute_metrics(m, principal=1_000_000, spread=0.02):
    lv = m[["sofr_comp", "usdkrw_end"]].dropna()
    ch = m[["d_sofr", "fx_logret"]].dropna()
    ch = ch.loc[m["d_sofr"].notna()]
    valid = m.dropna(subset=["d_cost"])

    max_resid = float(valid["decomp_residual"].abs().max())
    assert max_resid < 1e-6, f"기여도 합 ≠ C 변화 (오차 {max_resid})"

    return {
        "period": {"start_month": m["month"].iloc[0], "end_month": m["month"].iloc[-1]},
        "assumptions": {"principal_usd": principal, "spread": spread},
        "counts": {
            "endpoints": len(m) + 1,
            "periods": len(m),
            "changes": len(valid),
        },
        "corr_level": round(float(lv["sofr_comp"].corr(lv["usdkrw_end"])), 4),
        "corr_change": round(float(ch["d_sofr"].corr(ch["fx_logret"])), 4),
        "n_level": len(lv),
        "n_change": len(ch),
        "fx_dominant_months": int((valid["dominant"] == "FX").sum()),
        "rate_dominant_months": int((valid["dominant"] == "RATE").sum()),
        "sum_abs_contrib_rate_krw": round(float(valid["contrib_rate"].abs().sum()), 0),
        "sum_abs_contrib_fx_krw": round(float(valid["contrib_fx"].abs().sum()), 0),
        "max_decomp_residual": max_resid,
        "definitions": {
            "sofr_comp": "(SOFRINDEX_end/SOFRINDEX_start - 1) * 360 / actual days",
            "fx_logret": "ln(USDKRW_end / USDKRW_prev_end)",
            "corr_level": "corr(sofr_comp, usdkrw_end)",
            "corr_change": "corr(diff(sofr_comp), fx_logret) same month",
            "cost_30d_krw": "principal * (sofr_comp + spread) / 12 * usdkrw_end",
            "contrib": "symmetric decomposition, contrib_rate + contrib_fx = d_cost",
        },
    }

# ===============================================================
# 5) 보조 지표 (보고서 검산용) - metrics.json "auxiliary"와 같은 값
#    사용: aux = compute_auxiliary(m, daily, n_daily, SERIES, 1_000_000, 0.02, "2026-10-02", "manual")
# ===============================================================
import hashlib
from datetime import datetime
from pathlib import Path

SUBPERIODS = {"2020_2022": ("2020-01", "2022-12"), "2023_2025": ("2023-01", "2025-12")}
REGIMES = [
    ("저금리기", "low", "2020-01", "2022-02"),
    ("인상기", "hiking", "2022-03", "2023-07"),
    ("고금리 유지기", "hold", "2023-08", "2024-08"),
    ("인하기", "cutting", "2024-09", "2025-12"),
]


def _months(m, s, e):
    return m[(m["month"] >= s) & (m["month"] <= e)]


def _corr_pair(df):
    lv = df[["sofr_comp", "usdkrw_end"]].dropna()
    ch = df[["d_sofr", "fx_logret"]].dropna()
    return (round(float(lv["sofr_comp"].corr(lv["usdkrw_end"])), 4), len(lv),
            round(float(ch["d_sofr"].corr(ch["fx_logret"])), 4), len(ch))


def aux_subperiod_corr(m, subperiods=SUBPERIODS, exclude_month="2020-03"):
    sub = {}
    for key, (s, e) in subperiods.items():
        cl, nl, cc, nc = _corr_pair(_months(m, s, e))
        sub[key] = {"start": s, "end": e, "corr_level": cl, "n_level": nl, "corr_change": cc, "n_change": nc}
    cl, nl, cc, nc = _corr_pair(m[m["month"] != exclude_month])
    sub["excl_2020_03"] = {"corr_change": cc, "n_change": nc}
    return {**sub, "definition": "full monthly table sliced by month; d_sofr of first month uses previous month (2023-01 uses 2022-12)"}


def aux_regime_contrib(m, regimes=REGIMES):
    reg = {}
    for ko, key, s, e in regimes:
        part = _months(m, s, e)
        v = part.dropna(subset=["d_cost"])
        reg[key] = {"name": ko, "start": s, "end": e, "months": len(part), "change_months": len(v),
                    "net_contrib_rate_krw": round(float(v["contrib_rate"].sum()), 0),
                    "net_contrib_fx_krw": round(float(v["contrib_fx"].sum()), 0),
                    "net_d_cost_krw": round(float(v["d_cost"].sum()), 0),
                    "avg_cost_30d_krw": round(float(part["cost_30d_krw"].mean()), 0)}
    tot_r = sum(r["net_contrib_rate_krw"] for r in reg.values())
    tot_f = sum(r["net_contrib_fx_krw"] for r in reg.values())
    tot_c = sum(r["net_d_cost_krw"] for r in reg.values())
    c_first, c_last = m["cost_30d_krw"].iloc[0], m["cost_30d_krw"].iloc[-1]
    offset = -reg["cutting"]["net_contrib_fx_krw"] / reg["cutting"]["net_contrib_rate_krw"]
    return {"regimes": reg,
            "total": {"net_contrib_rate_krw": tot_r, "net_contrib_fx_krw": tot_f, "net_d_cost_krw": tot_c,
                      "cost_first_krw": round(float(c_first), 0), "cost_last_krw": round(float(c_last), 0),
                      "rate_share": round(float(tot_r / tot_c), 4), "cutting_fx_offset_ratio": round(float(offset), 4)},
            "definition": "sum of monthly contributions by month in regime; low regime starts at 2020-02 (first change month)"}


def aux_top_contrib(m, n=3):
    v = m.dropna(subset=["d_cost"])
    cols = ["month", "contrib_rate", "contrib_fx", "d_cost"]
    top = {}
    for key, col in [("rate", "contrib_rate"), ("fx", "contrib_fx")]:
        t = v.loc[v[col].abs().nlargest(n).index, cols]
        top[key] = [{"rank": i + 1, "month": r.month,
                     "contrib_rate_krw": round(float(r.contrib_rate), 0),
                     "contrib_fx_krw": round(float(r.contrib_fx), 0),
                     "d_cost_krw": round(float(r.d_cost), 0)} for i, r in enumerate(t.itertuples())]
    return {**top, "definition": "ranked by absolute monthly contribution, 71 change months"}


def aux_data_provenance(series_paths, download_date, method):
    dl = {"fred_download_date": download_date, "method": method, "files": {}}
    for sid, path in series_paths.items():
        p = Path(path)
        raw = pd.read_csv(p)
        dl["files"][sid] = {
            "path": path,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "rows": len(raw),
            "first_date": str(raw.iloc[0, 0]),
            "last_date": str(raw.iloc[-1, 0]),
            "file_modified_in_runtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
    return dl


def aux_concentration(m, top_n=10):
    v = m.dropna(subset=["d_cost"])
    out = {}
    for key, col in [("rate", "contrib_rate"), ("fx", "contrib_fx")]:
        a = v[col].abs()
        out[key] = {"top10_share": round(float(a.nlargest(top_n).sum() / a.sum()), 4),
                    "median_abs_krw": round(float(a.median()), 0),
                    "median_signed_krw": round(float(v[col].median()), 0)}
    out["definition"] = "top10_share = sum of 10 largest |contrib| / sum |contrib| (71 months); median of |contrib|"
    return out


def aux_yearly_dominance(m):
    v = m.dropna(subset=["d_cost"])
    yr = pd.crosstab(v["month"].str[:4], v["dominant"]).reindex(columns=["RATE", "FX", "TIE"], fill_value=0)
    return {y: {"rate": int(r["RATE"]), "fx": int(r["FX"]), "tie": int(r["TIE"])} for y, r in yr.iterrows()}


def _cluster(m, mask):
    c = m[mask]
    if c.empty:
        return {"months": 0}
    span = (pd.Period(c["month"].iloc[-1], "M") - pd.Period(c["month"].iloc[0], "M")).n + 1
    return {"months": len(c), "first": c["month"].iloc[0], "last": c["month"].iloc[-1],
            "contiguous": span == len(c),
            "fx_min": round(float(c["usdkrw_end"].min()), 2), "fx_max": round(float(c["usdkrw_end"].max()), 2),
            "corr_level": round(float(c["sofr_comp"].corr(c["usdkrw_end"])), 4) if len(c) > 2 else None}


def aux_scatter_clusters(m, low_pct=0.5, high_pct=3.0):
    return {"low_sofr_lt_0.5pct": _cluster(m, m["sofr_comp_pct"] < low_pct),
            "high_sofr_ge_3pct": _cluster(m, m["sofr_comp_pct"] >= high_pct),
            "between_months": m.loc[(m["sofr_comp_pct"] >= low_pct) & (m["sofr_comp_pct"] < high_pct), "month"].tolist()}


def aux_sensitivity(m, principal=1_000_000, spread=0.02, d_rate=0.01, fx_shock=0.10):
    last = m.iloc[-1]
    r, F = float(last["sofr_comp"]), float(last["usdkrw_end"])
    cost = lambda rr, FF: principal * (rr + spread) / 12 * FF
    base, s_r, s_f, s_b = cost(r, F), cost(r + d_rate, F), cost(r, F * (1 + fx_shock)), cost(r + d_rate, F * (1 + fx_shock))
    cross = (s_b - base) - (s_r - base) - (s_f - base)
    return {"sofr_comp": round(r, 6), "usdkrw": F,
            "base_krw": round(base), "sofr_plus_1pp_krw": round(s_r), "fx_plus_10pct_krw": round(s_f),
            "both_krw": round(s_b), "cross_term_krw": round(cross)}


def aux_regime_levels(m, regimes=REGIMES):
    out = {}
    for ko, key, s, e in regimes:
        p = _months(m, s, e)
        sp, fx = p["sofr_comp_pct"], p["usdkrw_end"]
        d = {"sofr_mean_pct": float(sp.mean()),
             "sofr_first_pct": float(sp.iloc[0]), "sofr_last_pct": float(sp.iloc[-1]),
             "sofr_min_pct": float(sp.min()), "sofr_min_month": p.loc[sp.idxmin(), "month"],
             "sofr_max_pct": float(sp.max()), "sofr_max_month": p.loc[sp.idxmax(), "month"],
             "fx_min": float(fx.min()), "fx_min_month": p.loc[fx.idxmin(), "month"],
             "fx_max": float(fx.max()), "fx_max_month": p.loc[fx.idxmax(), "month"],
             "avg_cost_30d_krw": float(p["cost_30d_krw"].mean())}
        out[key] = {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in d.items()}
    return out


def aux_data_checks(m, daily, n_daily, series_paths, win_start="2019-12-01", win_end="2025-12-31"):
    win = lambda df: df[(df["date"] >= win_start) & (df["date"] <= win_end)]
    s_raw = win(read_fred_csv(series_paths["SOFRINDEX"], "SOFRINDEX"))
    f_raw = win(read_fred_csv(series_paths["DEXKOUS"], "DEXKOUS"))
    d_mar = float(m.set_index("month").loc["2020-03", "d_sofr"]) * 100 if "2020-03" in set(m["month"]) else None
    return {"raw_missing_sofrindex": int(s_raw.iloc[:, 1].isna().sum()),
            "raw_missing_dexkous": int(f_raw.iloc[:, 1].isna().sum()), "common_days_all": len(win(daily)),
            "common_days_2020_2025": n_daily, "d_sofr_2020_03_pp": round(d_mar, 4) if d_mar is not None else None}


def compute_auxiliary(m, daily, n_daily, series_paths, principal=1_000_000, spread=0.02,
                      download_date="2026-10-02", method="manual"):
    """metrics.json 'auxiliary' 전체를 한 번에 계산 (보고서 v0.3 보조 지표·검산 수치)."""
    m = m.copy()
    m["month"] = m["month"].astype(str)
    return {
        "subperiod_corr": aux_subperiod_corr(m),
        "regime_contrib": aux_regime_contrib(m),
        "top3_contrib": aux_top_contrib(m),
        "data_provenance": aux_data_provenance(series_paths, download_date, method),
        "concentration": aux_concentration(m),
        "yearly_dominance": aux_yearly_dominance(m),
        "scatter_clusters": aux_scatter_clusters(m),
        "sensitivity_2025_12": aux_sensitivity(m, principal, spread),
        "regime_levels": aux_regime_levels(m),
        "data_checks": aux_data_checks(m, daily, n_daily, series_paths),
    }
