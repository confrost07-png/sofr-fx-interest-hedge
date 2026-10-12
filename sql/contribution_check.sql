-- data/processed/monthly_analysis.csv 를 monthly 테이블로 적재한 뒤 실행 (sql/run_sql.py)

-- 1) 연도별 이자 변화와 금리·환율 기여 (단위: 만원)
SELECT SUBSTR(month, 1, 4)                    AS 연도,
       ROUND(SUM(d_cost) / 10000, 0)          AS 이자변화,
       ROUND(SUM(contrib_rate) / 10000, 0)    AS 금리기여,
       ROUND(SUM(contrib_fx) / 10000, 0)      AS 환율기여,
       SUM(dominant = 'FX')                   AS 환율우세_개월,
       SUM(dominant = 'RATE')                 AS 금리우세_개월
FROM monthly
WHERE d_cost IS NOT NULL
GROUP BY 연도
ORDER BY 연도;

-- 2) 전체 기간 합계: 늘어난 이자 중 금리 몫
SELECT ROUND(SUM(d_cost) / 10000, 0)                         AS 늘어난_이자_만원,
       ROUND(SUM(contrib_rate) / 10000, 0)                   AS 금리기여_만원,
       ROUND(SUM(contrib_fx) / 10000, 0)                     AS 환율기여_만원,
       ROUND(SUM(contrib_rate) * 100.0 / SUM(d_cost), 1)     AS 금리몫_pct
FROM monthly
WHERE d_cost IS NOT NULL;

-- 3) 분해 검산: 금리 기여 + 환율 기여 = 이자 변화 (오차가 가장 큰 달)
SELECT month, ABS(d_cost - contrib_rate - contrib_fx) AS 분해오차_원
FROM monthly
WHERE d_cost IS NOT NULL
ORDER BY 분해오차_원 DESC
LIMIT 1;
