# API 명세서 v1
MODULE04 미니프로젝트03 · 2조 · 복리 SOFR·환율이 원화 이자부담에 미친 기여

이 문서는 프로젝트가 데이터를 받는 **외부 API**(FRED)와, 앱·노트북·스크립트가 함께 쓰는 **내부 인터페이스**(계산 함수, 데이터 파일, 결과 JSON 형식)를 정리한 것입니다. 각 항목에 코드 위치를 적었습니다.

> 이 프로젝트는 **API 키 없이** 동작합니다. FRED API 키는 선택 사항이며 제출물에 넣지 않았습니다(노트북의 `FRED_API_KEY = ""`). 투자 권유가 아닙니다. 상관관계는 인과관계가 아닙니다.

---

## 0. 한눈에 보기

| 구분 | 이름 | 용도 | 키 | 위치 |
|---|---|---|---|---|
| 외부 | FRED 그래프 CSV `fredgraph.csv` | 시계열 원본 CSV 내려받기 (**기본 방법**) | 필요 없음 | `source/01_data_pipeline.ipynb`, `source/robustness_check.py` `ensure_controls()` |
| 외부 | FRED API `series/observations` | 위 방법이 막힐 때 쓰는 예비 경로 (JSON) | `FRED_API_KEY` (선택) | `source/01_data_pipeline.ipynb` `download_all()` |
| 내부 | `analysis_core` 모듈 | 원본 읽기, 공통일 결합, 월말 끝점, 월별 테이블, 상관·기여도 지표 | - | `source/analysis_core.py` |
| 내부 | `robustness_check` 모듈 | 유의확률·신뢰구간·BH 보정·회귀·분해 민감도 | - | `source/robustness_check.py` |
| 내부 | 앱 계산 함수 | 사이드바 조건으로 재계산, p값·신뢰구간 | - | `app/app.py` |
| 내부 | 결과 파일 | `metrics.json`, `robustness.json`, 월별 CSV | - | `output/`, `data/processed/` |

**원본이 이미 들어 있습니다.** `data/raw/`에 FRED 원본 CSV 5개가 있어서 앱과 스크립트는 인터넷 없이 돌아갑니다. FRED는 원본이 없거나 `FORCE_DOWNLOAD = True`일 때만 부릅니다.

---

## 1. FRED 그래프 CSV (기본 경로)

| 항목 | 내용 |
|---|---|
| URL | `GET https://fred.stlouisfed.org/graph/fredgraph.csv` |
| 인증 | 없음 |
| 응답 | CSV 텍스트 (첫 줄 머리글) |
| 요청 헤더 | `User-Agent: Mozilla/5.0` (노트북). 일부 환경에서 기본 헤더를 막는 경우 대비 |
| 타임아웃 | 요청당 15초, 노트북 전체 40초 (`REQ_TIMEOUT`, `TOTAL_BUDGET`) |

**요청 파라미터**

| 이름 | 필수 | 이 프로젝트 값 | 뜻 |
|---|---|---|---|
| `id` | Y | `SOFRINDEX`, `DEXKOUS`, `DTWEXBGS`, `VIXCLS`, `IR3TIB01KRM156N` | 시계열 ID |
| `cosd` | N | `2019-12-01` | 조회 시작일 (2020-01 첫 달의 시작점인 2019년 12월 말이 들어가도록) |
| `coed` | N | `2025-12-31` | 조회 종료일 |

예: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFRINDEX&cosd=2019-12-01&coed=2025-12-31`

**응답 예** (`data/raw/sofrindex_fred.csv` 첫 줄)

```text
observation_date,SOFRINDEX
2019-12-02,1.03675814
2019-12-03,1.03680508
```

| 열 | 형식 | 설명 |
|---|---|---|
| `observation_date` (예전 형식은 `DATE`) | YYYY-MM-DD | 관측일 |
| `{시계열 ID}` | 숫자 또는 빈칸·`.` | 값. 휴일 등 결측은 빈칸 또는 `.` |

**받는 시계열**

| ID | 이름 | 원 출처 | 주기·단위 | 저장 파일 | 행 수 | 역할 |
|---|---|---|---|---|---|---|
| `SOFRINDEX` | SOFR Index | 뉴욕 연준 | 일별, 지수(2018-04-02 = 1) | `data/raw/sofrindex_fred.csv` | 1,588 | 두 월말 사이 지수 비율로 기간 복리 SOFR 계산 |
| `DEXKOUS` | 원/달러 환율 | 미 연준 H.10 | 일별, 1달러당 원 | `data/raw/dexkous_fred.csv` | 1,588 | 월말 원화 환산, 환율 변화율 |
| `DTWEXBGS` | 광의 달러지수 | 미 연준 | 일별, 지수(2006-01 = 100) | `data/raw/dtwexbgs_fred.csv` | 1,588 | 강건성 점검 통제변수 |
| `VIXCLS` | VIX | CBOE | 일별, 지수 | `data/raw/vixcls_fred.csv` | 1,588 | 강건성 점검 통제변수 |
| `IR3TIB01KRM156N` | 한국 3개월 은행간 금리 | OECD | 월평균, 연 % | `data/raw/ir3tib01krm156n_fred.csv` | 73 | 강건성 점검 통제변수 (한미 금리차) |

**응답 검사** (노트북 `_try()`): 응답 앞부분에 시계열 ID가 없거나 HTML 페이지(`<html`)이면 CSV가 아닌 것으로 보고 버립니다. 시계열마다 먼저 성공한 응답 하나만 저장합니다.

---

## 2. FRED API `series/observations` (예비 경로, 선택)

키가 있을 때만 노트북이 그래프 CSV와 **동시에** 요청합니다. 기본 설정(`FRED_API_KEY = ""`)에서는 호출하지 않습니다.

| 항목 | 내용 |
|---|---|
| URL | `GET https://api.stlouisfed.org/fred/series/observations` |
| 키 발급 | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) (무료) |

| 파라미터 | 값 |
|---|---|
| `series_id` | 1장의 시계열 ID |
| `api_key` | `FRED_API_KEY` (32자). **노트북을 공유하기 전에 반드시 비웁니다** |
| `file_type` | `json` |
| `observation_start` / `observation_end` | `2019-12-01` / `2025-12-31` |

**응답과 변환**

```json
{"observations": [{"date": "2019-12-02", "value": "1.03675814"}, {"date": "2019-12-25", "value": "."}]}
```

`observations[]`의 `date`, `value`만 꺼내 1장과 같은 CSV(`observation_date,{ID}`)로 바꾸고, 결측 `.`은 빈칸으로 바꿔 저장합니다. 그래서 이후 계산은 어느 경로로 받아도 같습니다.

---

## 3. 다운로드 오류 처리

| 상황 | 처리 |
|---|---|
| `data/raw/`에 원본이 있음 | **호출하지 않음** (기본). 새로 받으려면 노트북 `FORCE_DOWNLOAD = True` |
| 요청 1개가 15초 안에 응답 없음 | 그 요청만 실패 처리, 같은 시계열의 다른 주소(기간 지정 / 기간 미지정 / API)는 계속 |
| 전체 40초 초과 | 다운로드를 멈추고 저장된 원본으로 진행 (원본도 없으면 오류 안내) |
| 응답이 CSV가 아님 (HTML 오류 페이지 등) | 버림 |
| 결측(휴일 빈칸, `.`) | `NaN`으로 읽고 **직전 값으로 채우지 않음** (`read_fred_csv`) |
| 통제변수 원본 없음 | `robustness_check.ensure_controls()`가 1장 URL로 받음 (요청당 15초) |

**출처 기록**: 내려받은 날짜와 파일 sha256을 `metrics.json` → `auxiliary.data_provenance`, `robustness.json` → `controls`에 남겨, 나중에 같은 원본인지 확인할 수 있습니다. 조회일: 기본 자료 2026-10-02, 통제변수 2026-10-06.

---

## 4. 내부 인터페이스: `source/analysis_core.py`

앱(`app/app.py`)과 노트북 01·02가 **같은 함수**를 불러 숫자가 어긋나지 않게 했습니다.

| 함수 | 입력 | 출력 | 설명 |
|---|---|---|---|
| `read_fred_csv(path, series_id)` | CSV 경로, 시계열 ID | DataFrame `[date, {id 소문자}]` | `observation_date`/`DATE` 두 형식 지원, 결측은 NaN |
| `merge_daily(sofr, fx)` | 위 결과 2개 | DataFrame `[date, sofrindex, dexkous]` | 두 값이 모두 있는 날만 (inner join). 2020~2025 공통일 1,493일 |
| `month_end_points(daily, start_month, end_month)` | 일별 결합본, `"YYYY-MM"` 2개 | 월말 끝점 DataFrame | 시작월 직전 달 말 ~ 종료월 말의 매월 마지막 공통 관측일. 빠진 달이 있으면 `ValueError` |
| `build_monthly(daily, start_month, end_month, principal, spread)` | 기본값 `"2020-01"`, `"2025-12"`, `1_000_000`, `0.02` | 월별 분석 테이블 (5장 열) | 복리 SOFR, 환율 로그변화, 표준 30일 이자, 대칭분해 기여도 |
| `compute_metrics(m, principal, spread)` | 월별 테이블 | dict (`metrics.json`의 기본 지표) | 수준값·변화값 상관, 우세 월, 기여 절댓값 합. 분해 오차가 1e-6원 이상이면 `AssertionError` |
| `compute_auxiliary(m, daily, n_daily, series_paths, ...)` | 월별 테이블, 일별 자료 등 | dict (`metrics.json`의 `auxiliary`) | 기간별 상관, 국면별 기여, 민감도, 데이터 점검 |

**계산식** (코드 그대로)

| 항목 | 식 |
|---|---|
| 기간 복리 SOFR (연율) | `(SOFRINDEX_end / SOFRINDEX_start - 1) × 360 / 실제 경과일수` |
| 환율 로그변화 | `ln(USDKRW_end / USDKRW_start)` |
| 표준 30일 원화 이자 | `원금 × (복리 SOFR + 가산금리) / 12 × 월말 환율` |
| 금리 기여 | `원금 / 12 × (r - r0) × (F + F0) / 2` |
| 환율 기여 | `원금 / 12 × (F - F0) × ((r + s) + (r0 + s)) / 2` |
| 검산 | `d_cost - (금리 기여 + 환율 기여)` = 0 (71개월 모두 오차 0.000000002원 이하) |

(r = 복리 SOFR, F = 월말 환율, s = 가산금리, 0은 전월 값)

---

## 5. 내부 인터페이스: 앱 `app/app.py`

| 함수 | 입력 | 출력 | 설명 |
|---|---|---|---|
| `load_daily()` | 없음 (`data/raw/` 원본 2개) | 일별 결합본 | `@st.cache_data`로 한 번만 읽음 |
| `run_analysis(start, end, principal, spread)` | 사이드바 값 | `(월별 테이블, 지표 dict)` | **전체 기간으로 계산한 뒤 잘라 씀** (시작월의 변화값도 전월 대비로 계산돼 보고서와 같음) |
| `corr_test(xs, ys)` | 두 배열 | `(p값, (하한, 상한))` | 피어슨 r의 t검정 p값, 피셔 z 95% 신뢰구간. 표본 4개 미만이면 `None` |
| `robustness_text()` | `output/robustness.json` | 마크다운 문자열 | 자료·해석 안내 탭의 강건성 요약. 파일이 없으면 실행 안내만 표시 |

**사이드바 입력**

| 위젯 | 범위·기본값 | 바뀌는 것 |
|---|---|---|
| 분석 기간 (시작월 ~ 종료월) | 2020-01 ~ 2025-12, 월 단위 | 모든 카드·그래프·표. 3개월 미만이면 경고 후 멈춤, p값·신뢰구간은 4개월 이상 |
| 가상 원금 (USD) | 100,000 ~ 50,000,000, 기본 1,000,000 | 이자·기여 금액 |
| 고정 가산금리 (연 %p) | 0 ~ 10, 0.25 단위, 기본 2.00 | 이자·기여 금액 |
| 상관 기준 | 수준값 / 변화값 | 상관분석 탭의 카드와 산점도 |

---

## 6. 내부 인터페이스: `source/robustness_check.py`

| 함수 | 설명 |
|---|---|
| `ensure_controls(raw_dir, download=False)` | 통제변수 원본 3개 확인, 없으면 FRED 그래프 CSV로 받음 |
| `load_data(base)` | 월별 테이블 + 통제변수 결합 (`monthly_with_controls.csv`와 같은 표) |
| `fisher_ci(r, n)` | 피셔 z 95% 신뢰구간 |
| `perm_p(x, y, rng, B=10000)` | 순열검정 p값 |
| `block_boot_ci(x, y, rng, L=6, B=5000)` | 6개월 블록 부트스트랩 95% 구간 (자기상관 유지) |
| `corr_set(name, x, y, rng)` | 피어슨·스피어만·켄달 r, p, 신뢰구간, 순열 p, 블록 구간을 한 번에 |
| `fit_reg(formula, data, hac_lags=6)` | OLS + Newey-West HAC 표준오차 (시차 6) |
| `chow_test(data, formula, split)` | 구조 변화 검정 |
| `decomposition_sensitivity(m, ...)` | 분해 방식·시작 달을 바꿨을 때 금리 몫 |
| `run_all(base, out_dir, fig_dir, processed_out)` | 전체 실행 → `robustness.json`, 그림 4종. 시드 42 고정 |

실행: `python source/robustness_check.py` (약 20초). 노트북 03은 이 함수들을 그대로 불러 결과가 제출본과 같은지 확인합니다(상대오차 100만분의 1 이내).

---

## 7. 결과 파일 형식

### 7-1. `data/processed/monthly_analysis.csv` (72행, 숫자 기준 파일)

| 열 | 뜻 | 단위 |
|---|---|---|
| `month` | 이자 계산 기간의 달 | YYYY-MM |
| `start_date`, `end_date` | 기간 시작·끝 공통 관측일 | 날짜 |
| `sofr_index_start`, `sofr_index_end` | 시작·끝 SOFR Index | 지수 |
| `usdkrw_start`, `usdkrw_end` | 시작·끝 원/달러 | 원 |
| `days` | 실제 경과일수 | 일 |
| `sofr_comp`, `sofr_comp_pct` | 기간 복리 SOFR (연율) | 소수 / % |
| `fx_logret` | 환율 로그변화 | 소수 |
| `cost_30d_krw` | 표준 30일 원화 이자 | 원 |
| `d_sofr`, `d_cost` | 전월 대비 복리 SOFR 변화, 이자 변화 (첫 달 빈칸) | 소수 / 원 |
| `contrib_rate`, `contrib_fx` | 금리 기여, 환율 기여 | 원 |
| `decomp_residual` | 분해 검산 오차 | 원 |
| `dominant` | 그달 기여가 큰 쪽 (`RATE` / `FX` / `TIE`) | 문자 |
| `interest_actual_usd`, `interest_actual_krw` | 실제 경과일수 기준 이자 (참고) | 달러 / 원 |

`monthly_with_controls.csv`는 여기에 `usd_idx`, `vix`, `kr3m`, `diff_rate`(SOFR - 한국 3M, %p), `regime`(low < 0.5% ≤ mid < 3% ≤ high)을 붙인 표입니다.

### 7-2. `output/metrics.json` (v1.2)

| 키 | 형식 | 예 (전체 기간) |
|---|---|---|
| `period` | `{start_month, end_month}` | 2020-01, 2025-12 |
| `assumptions` | `{principal_usd, spread}` | 1000000, 0.02 |
| `counts` | `{endpoints, periods, changes, daily_common_2020_2025}` | 73, 72, 71, 1493 |
| `corr_level`, `n_level` | 수준값 상관, 표본 | 0.8011, 72 |
| `corr_change`, `n_change` | 변화값 상관, 표본 | -0.0517, 71 |
| `fx_dominant_months`, `rate_dominant_months` | 우세 월 수 | 44, 27 |
| `sum_abs_contrib_rate_krw`, `sum_abs_contrib_fx_krw` | 기여 절댓값 합 (원) | 9723189, 7995928 |
| `max_decomp_residual` | 분해 최대 오차 (원) | 2.25e-09 |
| `definitions`, `sources` | 계산식 문자열, 출처 | |
| `auxiliary` | 보조 지표: `subperiod_corr`, `regime_contrib`, `top3_contrib`, `data_provenance`, `concentration`, `yearly_dominance`, `scatter_clusters`, `sensitivity_2025_12`, `regime_levels`, `data_checks` | |
| `version`, `generated_at`, `changelog` | 버전 기록 | v1.2 |

### 7-3. `output/robustness.json` (v1.0)

| 키 | 내용 |
|---|---|
| `correlations[]` | 상관 12세트. 원소: `name, n, r, p, ci95, spearman, p_spearman, kendall, p_kendall, perm_p, block_boot_ci95, q_bh` |
| `multiple_testing` | `m`(검정 31개), `raw_p_lt_005`(11), `bh_significant`(8), `by_significant`(7), `bonferroni_significant`(7), `tests[]`(검정별 `p, q_bh, q_by, p_bonferroni`) |
| `regressions` | `L1`~`L5`(수준값), `C1`~`C4`(변화값), `regime_only_r2`, `dollar_only`. 원소: `formula, coef, diag, label` |
| `stationarity`, `cointegration_eg_p`, `granger_p` | ADF·KPSS, 엥글-그레인저, 그레인저 결과 |
| `cross_correlation_change[]` | 시차 -3 ~ +3개월 교차상관 |
| `chow`, `fisher_z_two_periods`, `fx_dominant`, `power` | 구조 변화, 두 기간 상관 차이, 우세 월 이항검정, 검정력 |
| `out_of_sample_change` | 표본 밖 예측 RMSE (모델 vs 변화 0) |
| `decomposition` | 분해 방식별·시작 달별 금리 몫 |
| `controls` | 통제변수 파일, 조회일, sha256, 결합 방식 |
| `settings`, `seed` | HAC 시차 6, 블록 6개월, 부트스트랩 5,000회, 순열 10,000회, 시드 42 |

---

## 8. 원칙 요약

1. **원본 우선**: 저장된 FRED 원본을 먼저 쓰고, 원본 파일은 수정하지 않습니다.
2. **결측은 채우지 않음**: 휴일 빈칸을 직전 값으로 메우지 않고 공통 관측일만 씁니다.
3. **한 모듈, 같은 숫자**: 앱·노트북·스크립트가 `analysis_core`를 함께 써서 보고서·앱·발표 숫자가 같습니다.
4. **키는 제출하지 않음**: FRED API 키는 선택이고, 제출 노트북에서는 비워 두었습니다.
5. **재현 가능**: 순열검정·부트스트랩은 시드 42, 원본 sha256 기록.

## 9. 참고 링크

- [FRED SOFRINDEX](https://fred.stlouisfed.org/series/SOFRINDEX) · [DEXKOUS](https://fred.stlouisfed.org/series/DEXKOUS) · [DTWEXBGS](https://fred.stlouisfed.org/series/DTWEXBGS) · [VIXCLS](https://fred.stlouisfed.org/series/VIXCLS) · [IR3TIB01KRM156N](https://fred.stlouisfed.org/series/IR3TIB01KRM156N)
- [FRED API series/observations 문서](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)

실제 은행 청구액·손익·신용위험을 측정하지 않는 가상 분석이며, **투자 권유가 아닙니다.**
