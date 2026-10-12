"""
robustness_check.py - 2조 MINI PROJECT 03 강건성 점검 (담당: 원승연, 발표 후 검수 반영 v1.0)

목적: 발표에서 쓴 상관 결과가 다른 방법으로 다시 분석해도 버티는지 확인한다.
  - 상관마다 r · p값 · 표본 수 · 95% 신뢰구간 · 순위상관 · 순열 p · 블록 부트스트랩 구간
  - 검정 31개 전체에 벤자미니-호크버그(BH) 다중비교 보정
  - 정상성(ADF·KPSS) · 공적분 · 교차상관 · 그레인저 검정
  - 회귀 사다리 9개 (Newey-West HAC 표준오차) + 가정 진단
  - 기여도 분해 방식·시작 달 민감도

입력: data/processed/monthly_analysis.csv (제출본 월별 테이블)
      data/raw/dtwexbgs_fred.csv · vixcls_fred.csv · ir3tib01krm156n_fred.csv (통제변수, FRED)
출력: output/robustness.json, output/figures/robustness/*.png 4종,
      data/processed/monthly_with_controls.csv
실행: 프로젝트 최상위 폴더에서  python source/robustness_check.py
      (노트북 03_robustness_check.ipynb는 같은 함수를 source/_run/robustness/에 실행)

무작위 계산(순열·부트스트랩)은 시드 42로 고정해 몇 번을 돌려도 같은 값이 나온다.
투자 권유가 아니며, 상관·회귀는 인과를 뜻하지 않는다.
"""
import hashlib
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent.parent
CONTROL_SERIES = {
    "DTWEXBGS": "dtwexbgs_fred.csv",          # 광의 달러지수 (일별)
    "VIXCLS": "vixcls_fred.csv",              # VIX (일별)
    "IR3TIB01KRM156N": "ir3tib01krm156n_fred.csv",  # 한국 3개월 은행간 금리 (월평균)
}
CONTROL_DOWNLOAD_DATE = "2026-10-06"
VERSION = "robustness v1.0"
SEED = 42


# ---------------------------------------------------------------
# 0) 데이터 준비
# ---------------------------------------------------------------
def _read_fred(path, sid):
    d = pd.read_csv(path)
    d.columns = ["date", sid]
    d["date"] = pd.to_datetime(d["date"])
    d[sid] = pd.to_numeric(d[sid], errors="coerce")       # '.'(휴일)은 NaN
    return d.dropna().sort_values("date").reset_index(drop=True)


def ensure_controls(raw_dir, download=False):
    """통제변수 원본이 없으면 FRED에서 받는다 (기본은 저장된 원본 사용)."""
    raw_dir = Path(raw_dir)
    for sid, fname in CONTROL_SERIES.items():
        p = raw_dir / fname
        if p.exists() and not download:
            continue
        import urllib.request
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2019-12-01&coed=2025-12-31"
        with urllib.request.urlopen(url, timeout=15) as r:
            p.write_bytes(r.read())
        print(f"FRED에서 받음: {sid} → {p}")


def load_data(base=BASE):
    """월별 테이블에 통제변수를 붙인다. 일별 자료는 월말 공통일 기준 직전 값, 한국 금리는 같은 달 값."""
    base = Path(base)
    m = pd.read_csv(base / "data/processed/monthly_analysis.csv", encoding="utf-8-sig")
    m["end_date"] = pd.to_datetime(m["end_date"])
    m = m.sort_values("end_date").reset_index(drop=True)
    for sid, col in [("DTWEXBGS", "usd_idx"), ("VIXCLS", "vix")]:
        d = _read_fred(base / "data/raw" / CONTROL_SERIES[sid], sid).rename(columns={sid: col})
        m = pd.merge_asof(m, d, left_on="end_date", right_on="date", direction="backward").drop(columns="date")
    kr = _read_fred(base / "data/raw" / CONTROL_SERIES["IR3TIB01KRM156N"], "kr3m")
    kr["month"] = kr["date"].dt.strftime("%Y-%m")
    m = m.merge(kr[["month", "kr3m"]], on="month", how="left")
    m["fx"] = m["usdkrw_end"]
    m["sofr"] = m["sofr_comp_pct"]
    m["dsofr"] = m["sofr"].diff()                 # %p (d_sofr × 100과 같음)
    m["dlfx"] = m["fx_logret"] * 100              # %
    m["yr"] = m["month"].str[:4].astype(int)
    m["diff_rate"] = m["sofr"] - m["kr3m"]
    m["d_kr3m"] = m["kr3m"].diff()
    m["d_diff_rate"] = m["diff_rate"].diff()
    m["dl_usd"] = np.log(m["usd_idx"]).diff() * 100
    m["dl_vix"] = np.log(m["vix"]).diff()
    m["regime"] = np.select([m["sofr"] < 0.5, m["sofr"] < 3], ["low", "mid"], "high")
    m["late"] = (m["yr"] >= 2023).astype(int)
    return m


# ---------------------------------------------------------------
# 1) 상관 한 세트 (r, p, n, CI, 다른 방법)
# ---------------------------------------------------------------
def fisher_ci(r, n):
    z, se = np.arctanh(r), 1 / np.sqrt(n - 3)
    return float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))


def perm_p(x, y, rng, B=10000):
    r0 = np.corrcoef(x, y)[0, 1]
    rs = np.array([np.corrcoef(x, rng.permutation(y))[0, 1] for _ in range(B)])
    return float((np.sum(np.abs(rs) >= abs(r0)) + 1) / (B + 1))


def block_boot_ci(x, y, rng, L=6, B=5000):
    """6개월 덩어리로 다시 뽑아 자기상관을 살린 95% 구간."""
    n, idx0, out = len(x), np.arange(len(x)), []
    for _ in range(B):
        starts = rng.integers(0, n - L + 1, size=int(np.ceil(n / L)))
        idx = np.concatenate([idx0[s:s + L] for s in starts])[:n]
        out.append(np.corrcoef(x[idx], y[idx])[0, 1])
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def corr_set(name, x, y, rng):
    x, y = np.asarray(x, float), np.asarray(y, float)
    r, p = stats.pearsonr(x, y)
    rho, prho = stats.spearmanr(x, y)
    tau, ptau = stats.kendalltau(x, y)
    return {"name": name, "n": int(len(x)), "r": float(r), "p": float(p), "ci95": fisher_ci(r, len(x)),
            "spearman": float(rho), "p_spearman": float(prho), "kendall": float(tau), "p_kendall": float(ptau),
            "perm_p": perm_p(x, y, rng), "block_boot_ci95": block_boot_ci(x, y, rng)}


# ---------------------------------------------------------------
# 2) 회귀 (OLS + Newey-West HAC 시차 6) + 진단
# ---------------------------------------------------------------
REG_LEVEL = {
    "L1": ("SOFR만", "fx ~ sofr"),
    "L2": ("+ 한국금리·VIX", "fx ~ sofr + kr3m + vix"),
    "L3": ("+ 금리 국면 더미", "fx ~ sofr + C(regime, Treatment('low'))"),
    "L4": ("+ 2023년 이후 상호작용", "fx ~ sofr + late + sofr:late"),
    "L5": ("+ 한국금리·VIX·달러지수", "fx ~ sofr + kr3m + vix + usd_idx"),
}
REG_CHANGE = {
    "C1": ("SOFR 변화만", "dlfx ~ dsofr"),
    "C2": ("+ 한국금리·VIX 변화", "dlfx ~ dsofr + d_kr3m + dl_vix"),
    "C3": ("+ 2023년 이후 상호작용", "dlfx ~ dsofr + late + dsofr:late"),
    "C4": ("+ 달러지수 변화", "dlfx ~ dsofr + d_kr3m + dl_vix + dl_usd"),
}


def fit_reg(formula, data, hac_lags=6):
    import statsmodels.formula.api as smf
    from statsmodels.stats.diagnostic import acorr_breusch_godfrey, het_breuschpagan, linear_reset
    from statsmodels.stats.outliers_influence import OLSInfluence, variance_inflation_factor
    from statsmodels.stats.stattools import durbin_watson, jarque_bera

    o = smf.ols(formula, data).fit()
    h = o.get_robustcov_results(cov_type="HAC", maxlags=hac_lags)
    names = o.model.exog_names
    ci = h.conf_int()
    coef = {nm: {"b": float(o.params[nm]), "se_hac": float(h.bse[i]), "p_hac": float(h.pvalues[i]),
                 "ci95_hac": (float(ci[i][0]), float(ci[i][1])), "p_ols": float(o.pvalues[nm])}
            for i, nm in enumerate(names)}
    X = o.model.exog
    cook = OLSInfluence(o).cooks_distance[0]
    diag = {"n": int(o.nobs), "r2": float(o.rsquared), "adj_r2": float(o.rsquared_adj), "aic": float(o.aic),
            "durbin_watson": float(durbin_watson(o.resid)),
            "breusch_godfrey_p": float(acorr_breusch_godfrey(o, nlags=6)[1]),
            "breusch_pagan_p": float(het_breuschpagan(o.resid, X)[1]),
            "jarque_bera_p": float(jarque_bera(o.resid)[1]),
            "reset_p": float(linear_reset(o, power=2, use_f=True).pvalue),
            "vif": {nm: float(variance_inflation_factor(X, i)) for i, nm in enumerate(names)
                    if nm != "Intercept"} if X.shape[1] > 2 else {},
            "cook_top3": list(data.iloc[np.argsort(cook)[::-1][:3]]["month"])}
    return {"formula": formula, "coef": coef, "diag": diag}, o


def chow_test(data, formula, split):
    import statsmodels.formula.api as smf
    o = smf.ols(formula, data).fit()
    k = len(o.params)
    a = smf.ols(formula, data[data["end_date"] < split]).fit()
    b = smf.ols(formula, data[data["end_date"] >= split]).fit()
    F = ((o.ssr - a.ssr - b.ssr) / k) / ((a.ssr + b.ssr) / (len(data) - 2 * k))
    return {"split": split, "F": float(F), "p": float(1 - stats.f.cdf(F, k, len(data) - 2 * k))}


# ---------------------------------------------------------------
# 3) 기여도 분해 민감도
# ---------------------------------------------------------------
def decomposition_sensitivity(m, principal=1_000_000, spread=0.02):
    r, f, k = m["sofr_comp"].values, m["fx"].values, principal / 12
    r0, r1, f0, f1 = r[:-1], r[1:], f[:-1], f[1:]
    methods = {
        "symmetric": (k * (r1 - r0) * (f0 + f1) / 2, k * ((r0 + spread) + (r1 + spread)) / 2 * (f1 - f0)),
        "rate_first": (k * (r1 - r0) * f0, k * (r1 + spread) * (f1 - f0)),
        "fx_first": (k * (r1 - r0) * f1, k * (r0 + spread) * (f1 - f0)),
    }
    out = {}
    for nm, (cr, cf) in methods.items():
        out[nm] = {"net_rate_krw": round(float(cr.sum())), "net_fx_krw": round(float(cf.sum())),
                   "rate_share": round(float(cr.sum() / (cr.sum() + cf.sum())), 4),
                   "fx_dominant_months": int((np.abs(cf) > np.abs(cr)).sum())}
    lr, lf = np.log(r + spread), np.log(f)
    out["log_multiplicative"] = {"rate_share": round(float((lr[-1] - lr[0]) / ((lr[-1] - lr[0]) + (lf[-1] - lf[0]))), 4)}
    starts = {}
    cr, cf = methods["symmetric"]
    for st in ["2020-01", "2020-03", "2020-06", "2021-01", "2022-01"]:
        i = int(np.where(m["month"].values == st)[0][0])
        starts[st] = {"rate_share": round(float(cr[i:].sum() / (cr[i:].sum() + cf[i:].sum())), 4),
                      "net_change_krw": round(float(cr[i:].sum() + cf[i:].sum()))}
    out["start_month"] = starts
    out["definition"] = ("symmetric = submitted method (cross term split in half); rate_first/fx_first = sequential; "
                         "start_month = rate share of cumulative change when the first change month is moved")
    return out


# ---------------------------------------------------------------
# 4) 전체 실행
# ---------------------------------------------------------------
def run_all(base=BASE, out_dir=None, fig_dir=None, processed_out=None, verbose=True):
    from statsmodels.stats.multitest import multipletests
    from statsmodels.tsa.stattools import adfuller, coint, grangercausalitytests, kpss

    base = Path(base)
    out_dir = Path(out_dir) if out_dir else base / "output"
    fig_dir = Path(fig_dir) if fig_dir else out_dir / "figures/robustness"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    ensure_controls(base / "data/raw")
    m = load_data(base)
    if processed_out:
        cols = ["month", "end_date", "sofr_comp_pct", "usdkrw_end", "fx_logret", "usd_idx", "vix", "kr3m", "diff_rate", "regime"]
        m[cols].to_csv(processed_out, index=False, encoding="utf-8-sig")
    R = {"version": VERSION, "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED}
    tests = []
    d = m.dropna(subset=["dsofr"])

    # 재현
    R["reproduce"] = {"corr_level": round(float(m["sofr"].corr(m["fx"])), 4),
                      "corr_change": round(float(d["dsofr"].corr(d["fx_logret"])), 4),
                      "note": "metrics.json v1.2의 corr_level 0.8011, corr_change -0.0517과 같아야 한다"}

    # 상관
    corr = [corr_set("수준값 전체", m["sofr"], m["fx"], rng),
            corr_set("변화값 전체", d["dsofr"], d["fx_logret"], rng)]
    for a, b in [(2020, 2022), (2023, 2025)]:
        s = m[(m["yr"] >= a) & (m["yr"] <= b)]
        corr.append(corr_set(f"수준값 {a}-{str(b)[2:]}", s["sofr"], s["fx"], rng))
        s2 = s.dropna(subset=["dsofr"])
        corr.append(corr_set(f"변화값 {a}-{str(b)[2:]}", s2["dsofr"], s2["fx_logret"], rng))
    for g, mask in [("저금리(<0.5%)", m["sofr"] < 0.5), ("전환기(0.5~3%)", (m["sofr"] >= 0.5) & (m["sofr"] < 3)),
                    ("고금리(≥3%)", m["sofr"] >= 3)]:
        s = m[mask]
        corr.append(corr_set(f"수준값 {g}", s["sofr"], s["fx"], rng))
    s = d[d["month"] != "2020-03"]
    corr.append(corr_set("변화값 2020-03 제외", s["dsofr"], s["fx_logret"], rng))
    corr.append(corr_set("수준값 금리차(SOFR-한국3M) vs 환율", m["diff_rate"], m["fx"], rng))
    dd = m.dropna(subset=["d_diff_rate"])
    corr.append(corr_set("변화값 금리차 vs 환율", dd["d_diff_rate"], dd["fx_logret"], rng))
    tests += [(c["name"], c["p"]) for c in corr]

    lv = {c["name"]: c for c in corr}
    r1, r2 = lv["수준값 2020-22"]["r"], lv["수준값 2023-25"]["r"]
    z = (np.arctanh(r1) - np.arctanh(r2)) / np.sqrt(1 / 33 + 1 / 33)
    fisher = {"z": float(z), "p": float(2 * (1 - stats.norm.cdf(abs(z))))}
    tests.append(("두 기간 수준값 상관 차이(피셔 z)", fisher["p"]))

    xcorr = []
    for k in range(-3, 4):
        a = d["dsofr"].shift(k)
        ok = a.notna()
        r, p = stats.pearsonr(a[ok], d["fx_logret"][ok])
        xcorr.append({"lag": k, "r": float(r), "p": float(p), "n": int(ok.sum())})
        if k != 0:
            tests.append((f"교차상관 시차 {k:+d}", float(p)))

    fx_dom = int((m["contrib_fx"].abs() > m["contrib_rate"].abs()).sum())
    binom_p = float(stats.binomtest(fx_dom, 71, 0.5).pvalue)
    tests.append((f"환율 우세 {fx_dom}/71 이항검정", binom_p))

    def ak(x):
        x = pd.Series(x).dropna()
        with warnings.catch_warnings():          # KPSS p값은 표 범위(0.01~0.10) 밖이면 경계값으로 나옴
            warnings.simplefilter("ignore")
            kp = kpss(x, regression="c", nlags="auto")[1]
        return {"adf_p": float(adfuller(x, autolag="AIC")[1]), "kpss_p": float(kp)}
    stationarity = {c: ak(m[c]) for c in ["sofr", "fx", "dsofr", "dlfx"]}
    coint_p = float(coint(m["fx"], m["sofr"])[1])
    tests.append(("공적분(엥글-그레인저) 환율~SOFR", coint_p))
    granger = {}
    for lab, cols in [("SOFR→환율", ["fx_logret", "dsofr"]), ("환율→SOFR", ["dsofr", "fx_logret"])]:
        try:
            res = grangercausalitytests(d[cols].values, maxlag=3, verbose=False)
        except TypeError:                        # statsmodels 0.15부터 verbose 인자 없음
            res = grangercausalitytests(d[cols].values, maxlag=3)
        granger[lab] = {str(L): float(res[L][0]["ssr_ftest"][1]) for L in (1, 2, 3)}
        tests += [(f"그레인저 {lab} 시차{L}", granger[lab][str(L)]) for L in (1, 2, 3)]

    # 회귀
    lvl = m.dropna(subset=["kr3m", "vix", "usd_idx"]).copy()
    chg = m.dropna(subset=["dsofr", "d_kr3m", "dl_vix", "dl_usd"]).copy()
    reg, models = {}, {}
    for key, (label, f) in REG_LEVEL.items():
        reg[key], models[key] = fit_reg(f, lvl)
        reg[key]["label"] = label
    for key, (label, f) in REG_CHANGE.items():
        reg[key], models[key] = fit_reg(f, chg)
        reg[key]["label"] = label
    import statsmodels.formula.api as smf
    reg["regime_only_r2"] = float(smf.ols("fx ~ C(regime, Treatment('low'))", lvl).fit().rsquared)
    o_usd = smf.ols("dlfx ~ dl_usd", chg).fit()               # 달러지수 변화 하나만 (그림 change_drivers 오른쪽)
    reg["dollar_only"] = {"formula": "dlfx ~ dl_usd", "b": float(o_usd.params["dl_usd"]), "r2": float(o_usd.rsquared),
                          "p": float(o_usd.pvalues["dl_usd"]), "n": int(o_usd.nobs)}
    tests += [("회귀 L1 SOFR 계수(HAC)", reg["L1"]["coef"]["sofr"]["p_hac"]),
              ("회귀 C1 SOFR 변화 계수(HAC)", reg["C1"]["coef"]["dsofr"]["p_hac"]),
              ("회귀 C2 SOFR 변화 계수(HAC)", reg["C2"]["coef"]["dsofr"]["p_hac"])]
    chow = {"level_2023_01": chow_test(lvl, "fx ~ sofr", "2023-01-01"),
            "level_2022_03": chow_test(lvl, "fx ~ sofr", "2022-03-01")}
    tests.append(("Chow 수준값 2023-01", chow["level_2023_01"]["p"]))
    tr, te = chg[chg["end_date"] < "2024-07-01"], chg[chg["end_date"] >= "2024-07-01"]
    pred = smf.ols("dlfx ~ dsofr", tr).fit().predict(te)
    oos = {"train": "2020-02~2024-06", "test": "2024-07~2025-12", "n_test": int(len(te)),
           "rmse_model": float(np.sqrt(((te["dlfx"] - pred) ** 2).mean())),
           "rmse_zero_change": float(np.sqrt((te["dlfx"] ** 2).mean()))}

    # 다중비교 보정
    names, ps = zip(*tests)
    bh, by, bon = (multipletests(ps, method=mth) for mth in ("fdr_bh", "fdr_by", "bonferroni"))
    multi = [{"name": n, "p": float(p), "q_bh": float(q), "significant_bh": bool(s), "q_by": float(qy), "p_bonferroni": float(pb)}
             for n, p, q, s, qy, pb in zip(names, ps, bh[1], bh[0], by[1], bon[1])]
    qmap = {t["name"]: t["q_bh"] for t in multi}
    for c in corr:
        c["q_bh"] = qmap[c["name"]]

    n71 = len(d)
    R.update({
        "correlations": corr, "fisher_z_two_periods": fisher, "cross_correlation_change": xcorr,
        "fx_dominant": {"months": fx_dom, "of": 71, "binom_p": binom_p},
        "stationarity": stationarity, "cointegration_eg_p": coint_p, "granger_p": granger,
        "regressions": reg, "chow": chow, "out_of_sample_change": oos,
        "decomposition": decomposition_sensitivity(m),
        "power": {"n": n71, "detectable_r_80pct": float(np.tanh((1.96 + 0.8416) / np.sqrt(n71 - 3)))},
        "multiple_testing": {"m": len(ps), "raw_p_lt_005": int(sum(p < 0.05 for p in ps)),
                             "bh_significant": int(bh[0].sum()), "by_significant": int(by[0].sum()),
                             "bonferroni_significant": int(bon[0].sum()), "tests": multi},
        "controls": {"series": {k: f"data/raw/{v}" for k, v in CONTROL_SERIES.items()},
                     "download_date": CONTROL_DOWNLOAD_DATE,
                     "sha256": {k: hashlib.sha256((base / "data/raw" / v).read_bytes()).hexdigest()
                                for k, v in CONTROL_SERIES.items()},
                     "alignment": "daily series: last value on or before the month-end common date; KR 3M: same month",
                     "missing_after_merge": int(m[["usd_idx", "vix", "kr3m"]].isna().sum().sum())},
        "settings": {"hac_lags": 6, "block_length_months": 6, "bootstrap_draws": 5000, "permutations": 10000,
                     "regime_thresholds_pct": [0.5, 3.0], "late_period_from": "2023-01"},
    })
    with open(out_dir / "robustness.json", "w", encoding="utf-8") as fp:
        json.dump(R, fp, ensure_ascii=False, indent=1)
    make_figures(m, R, models, fig_dir)
    if verbose:
        print_summary(R)
    return R


def print_summary(R):
    print(f"[{R['version']}] 검정 {R['multiple_testing']['m']}개 · 보정 전 유의 {R['multiple_testing']['raw_p_lt_005']}개"
          f" → BH 보정 후 {R['multiple_testing']['bh_significant']}개")
    print(f"{'구분':<26}{'n':>4}{'r':>8}{'p':>10}{'BH q':>9}   95% CI         블록 부트스트랩")
    for c in R["correlations"]:
        print(f"{c['name']:<26}{c['n']:>4}{c['r']:>+8.3f}{c['p']:>10.2g}{c['q_bh']:>9.3g}   "
              f"{c['ci95'][0]:+.2f}~{c['ci95'][1]:+.2f}    {c['block_boot_ci95'][0]:+.2f}~{c['block_boot_ci95'][1]:+.2f}")
    L3 = R["regressions"]["L3"]["coef"]["sofr"]
    C4 = R["regressions"]["C4"]
    print(f"L3 국면 고정 시 SOFR 계수 {L3['b']:+.1f}원 (HAC p = {L3['p_hac']:.2f})")
    print(f"C4 달러지수 변화 계수 {C4['coef']['dl_usd']['b']:.2f} (R² {C4['diag']['r2']:.2f}), "
          f"SOFR 변화만 쓴 C1 R² {R['regressions']['C1']['diag']['r2']:.3f}")
    dec = R["decomposition"]
    print("금리 몫: 대칭 {:.1%} · 금리 먼저 {:.1%} · 환율 먼저 {:.1%} · 로그 {:.1%}".format(
        dec["symmetric"]["rate_share"], dec["rate_first"]["rate_share"], dec["fx_first"]["rate_share"],
        dec["log_multiplicative"]["rate_share"]))


# ---------------------------------------------------------------
# 5) 그림 4종
# ---------------------------------------------------------------
def make_figures(m, R, models, fig_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    import statsmodels.formula.api as smf

    for name in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]:
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams.update({"axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e6e6e6", "grid.linewidth": .8})
    BLUE, RED, GRAY, INK = "#1f5fa8", "#c0392b", "#8a8f98", "#222222"
    fig_dir = Path(fig_dir)

    def ptxt(p):
        return "p < 0.001" if p < .001 else (f"p = {p:.3f}" if p < .1 else f"p = {p:.2f}")

    # (1) 24개월 이동 상관
    roll_l = m["sofr"].rolling(24).corr(m["fx"])
    roll_c = m["dsofr"].rolling(24).corr(m["fx_logret"])
    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.axhline(0, color="#999", lw=1)
    ax.plot(m["end_date"], roll_l, color=RED, lw=2, label="수준값 상관 (24개월 이동)")
    ax.plot(m["end_date"], roll_c, color=BLUE, lw=2, label="변화값 상관 (24개월 이동)")
    ax.set_ylim(-1, 1)
    ax.set_yticks([-1, -.5, 0, .5, 1])
    ax.set_yticklabels(["-1.0", "-0.5", "0", "0.5", "1.0"])
    ax.set_title(f"수준값 상관은 {roll_l.max():+.2f}에서 {roll_l.min():+.2f}까지 뒤집히고, "
                 f"변화값 상관은 {roll_c.min():+.2f}~{roll_c.max():+.2f} 안에 머문다", fontsize=11.5, loc="left", color=INK)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.1), ncol=2, frameon=False)
    ax.text(1, -.28, "창 24개월, 첫 값은 2021-12 · 자료: FRED SOFRINDEX·DEXKOUS", transform=ax.transAxes,
            ha="right", fontsize=8.5, color="#777")
    fig.tight_layout()
    fig.savefig(fig_dir / "rolling_corr.png", dpi=170)
    plt.close(fig)

    # (2) 환율 변화: SOFR 변화 vs 달러지수 변화
    c = m.dropna(subset=["dsofr", "dl_usd"])
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 4.6), sharey=True)
    for ax, x, xl, col in [(axs[0], "dsofr", "SOFR 전월 대비 변화 (%p)", GRAY),
                           (axs[1], "dl_usd", "달러지수(광의) 변화율 (%)", BLUE)]:
        ax.scatter(c[x], c["dlfx"], s=40, color=col, edgecolor="white", linewidth=1.2, zorder=3)
        o = smf.ols(f"dlfx ~ {x}", c).fit()
        xs = np.linspace(c[x].min(), c[x].max(), 50)
        pr = o.get_prediction(pd.DataFrame({x: xs})).summary_frame()
        ax.fill_between(xs, pr["mean_ci_lower"], pr["mean_ci_upper"], color=col, alpha=.15, lw=0)
        ax.plot(xs, pr["mean"], color=INK if col == GRAY else col, lw=2)
        ax.set_xlabel(xl)
        ax.set_title(f"$R^2$ = {o.rsquared:.2f} · 기울기 {ptxt(o.pvalues[x])}", fontsize=10.5, loc="left")
    axs[0].set_ylabel("원/달러 월간 변화율 (%)")
    fig.suptitle(f"달마다 환율 변화를 설명한 건 SOFR가 아니라 글로벌 달러 강세였다 ({len(c)}개월)",
                 x=.01, ha="left", fontsize=12, color=INK)
    fig.text(.99, .01, "음영 = 회귀선 95% 신뢰대 · 달러지수 = FRED DTWEXBGS(원화가 일부 포함됨)",
             ha="right", fontsize=8.5, color="#777")
    fig.tight_layout(rect=(0, .03, 1, .95))
    fig.savefig(fig_dir / "change_drivers.png", dpi=170)
    plt.close(fig)

    # (3) 수준값 회귀 SOFR 계수 사다리
    rows = [("L1", "① SOFR만"), ("L2", "② + 한국금리·VIX"), ("L5", "③ + 달러지수"), ("L3", "④ + 금리 국면 더미")]
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    for i, (k, lab) in enumerate(rows):
        cc = R["regressions"][k]["coef"]["sofr"]
        lo, hi = cc["ci95_hac"]
        col = RED if lo > 0 else GRAY
        ax.plot([lo, hi], [i, i], color=col, lw=2.5, solid_capstyle="round")
        ax.scatter(cc["b"], i, s=70, color=col, edgecolor="white", linewidth=1.5, zorder=3)
        ax.text(hi + 3, i, f"{cc['b']:+.0f}원 ({ptxt(cc['p_hac'])})", va="center", fontsize=9.5, color=INK)
    ax.axvline(0, color="#999", lw=1)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[1] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(-80, 120)
    ax.set_xlabel("SOFR 1%p 높은 달의 원/달러 차이 (원, 수준값 회귀)")
    ax.set_title("금리 국면을 넣으면 SOFR 계수가 0과 구분되지 않는다 → +0.80은 국면 차이",
                 fontsize=11.5, loc="left", color=INK)
    fig.text(.99, .01, f"막대 = Newey-West(HAC, 시차 6) 95% 신뢰구간 · {R['regressions']['L1']['diag']['n']}개월",
             ha="right", fontsize=8.5, color="#777")
    fig.tight_layout(rect=(0, .03, 1, 1))
    fig.savefig(fig_dir / "coef_ladder.png", dpi=170)
    plt.close(fig)

    # (4) 잔차 자기상관
    o1, o2 = models["L1"], models["C1"]
    lvl = m.dropna(subset=["kr3m", "vix", "usd_idx"])
    chg = m.dropna(subset=["dsofr", "d_kr3m", "dl_vix", "dl_usd"])
    fig, axs = plt.subplots(2, 1, figsize=(10, 5.4), sharex=True)
    axs[0].bar(lvl["end_date"], o1.resid, width=20, color=RED)
    axs[0].axhline(0, color="#999", lw=1)
    axs[0].set_title(f"수준값 회귀 잔차: 같은 부호가 몇 년씩 이어짐 (Durbin-Watson "
                     f"{R['regressions']['L1']['diag']['durbin_watson']:.2f}, 2에 가까워야 정상)", fontsize=10.5, loc="left")
    axs[0].set_ylabel("원")
    axs[1].bar(chg["end_date"], o2.resid, width=20, color=BLUE)
    axs[1].axhline(0, color="#999", lw=1)
    axs[1].set_title(f"변화값 회귀 잔차(환율 변화율): 들쭉날쭉, 자기상관 거의 없음 (Durbin-Watson "
                     f"{R['regressions']['C1']['diag']['durbin_watson']:.2f})", fontsize=10.5, loc="left")
    axs[1].set_ylabel("%")
    fig.suptitle("수준값 상관·회귀의 p값은 믿기 어렵다: 잔차가 서로 연결돼 있다", x=.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, .95))
    fig.savefig(fig_dir / "residual_autocorr.png", dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    run_all(processed_out=BASE / "data/processed/monthly_with_controls.csv")
    print("저장: output/robustness.json, output/figures/robustness/ 4종, data/processed/monthly_with_controls.csv")
    sys.exit(0)
