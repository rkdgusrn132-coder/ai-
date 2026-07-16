# -*- coding: utf-8 -*-
"""
DART Open API로 소비재 기업 12개사의 재무정보(자산총계·매출액·부채총계·자본총계)와
감사보수정보(감사인·감사의견·감사보수·감사시간)를 수집하여 CSV 2개로 저장하는 부록 스크립트입니다.

메인 노트북(소비재_감사보수_결정요인_분석.ipynb)은 이 CSV 2개를 읽어서 분석을 시작합니다.

⚠️ 중요: '감사용역체결현황'(adtServcCnclsSttus) API는 이 코드를 작성한 환경에서
실제로 호출·검증하지 못했습니다. DART 공식 개발가이드에 명시된 요청 방식(엔드포인트,
필수 파라미터)은 정확하지만, 응답 JSON의 세부 필드명(예: 감사보수 컬럼명)은 실행 후
직접 확인하고 아래 COLUMN MAPPING 부분을 실제 필드명에 맞게 수정해야 할 수 있습니다.

실행 방법:
    1. 아래 API_KEY에 발급받은 키를 입력
    2. python dart_audit_fee_collector.py 실행 (인터넷 연결 필요)
    3. 처음 실행 시 audit_raw_sample.csv로 원본 응답을 먼저 저장하니,
       컬럼명을 확인한 뒤 COLUMN MAPPING 부분을 실제 컬럼명으로 수정하고 다시 실행하세요.
"""

import requests
import zipfile
import io
import time
import xml.etree.ElementTree as ET

import pandas as pd
import numpy as np

# ⚠️ 실제 발급받은 키로 교체하세요. 외부에 공유할 경우 반드시 삭제 후 공유하세요.
API_KEY = "본인의_DART_API_KEY를_입력하세요"

TARGET_COMPANIES = [
    "CJ제일제당", "오뚜기", "농심", "오리온", "하이트진로", "롯데칠성음료",
    "LG생활건강", "애경산업", "아모레퍼시픽", "한국콜마", "매일유업", "빙그레"
]
YEARS = list(range(2018, 2024))

FIN_OUTPUT_CSV = "소비재_재무정보.csv"
AUDIT_OUTPUT_CSV = "소비재_감사보수정보.csv"

# ⚠️ COLUMN MAPPING: 실제 API 응답을 확인한 후 아래 컬럼명을 필요시 수정하세요.
# adtServcCnclsSttus 응답에서 감사인명 / 보수 / 시간에 해당하는 컬럼명(추정치)
AUDITOR_COL_CANDIDATES = ["adtor", "corp_name_bfadtor", "cn"]
FEE_COL_CANDIDATES = ["mendng", "adt_mendng", "cntrct_mendng"]
HOURS_COL_CANDIDATES = ["adt_time", "cntrct_time", "time"]


def get_corp_code_map(api_key):
    """DART에 등록된 전체 기업의 고유번호(corp_code)를 조회하여 DataFrame으로 반환"""
    url = "https://opendart.fss.or.kr/api/corpCode.xml"
    res = requests.get(url, params={"crtfc_key": api_key})
    z = zipfile.ZipFile(io.BytesIO(res.content))
    xml_data = z.read(z.namelist()[0])
    root = ET.fromstring(xml_data)

    rows = []
    for node in root.findall("list"):
        rows.append({
            "corp_code": node.findtext("corp_code"),
            "corp_name": node.findtext("corp_name"),
            "stock_code": node.findtext("stock_code"),
        })
    corp_df = pd.DataFrame(rows)
    corp_df = corp_df[corp_df["stock_code"].str.strip() != ""].reset_index(drop=True)
    return corp_df


def get_financial_items(api_key, corp_code, year, reprt_code="11011"):
    """지정 연도 사업보고서에서 자산총계 / 매출액 / 부채총계 / 자본총계 금액을 추출"""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    df = None

    for fs_div in ["CFS", "OFS"]:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        res = requests.get(url, params=params)
        data = res.json()
        if data.get("status") == "000":
            df = pd.DataFrame(data["list"])
            break

    if df is None:
        return None

    def find_amount(keywords):
        for kw in keywords:
            row = df[df["account_nm"].str.contains(kw, na=False, regex=False)]
            if not row.empty:
                amt = row.iloc[0]["thstrm_amount"].replace(",", "")
                return float(amt) if amt not in ("", "-") else np.nan
        return np.nan

    return {
        "assets": find_amount(["자산총계"]),
        "revenue": find_amount(["매출액", "수익(매출액)"]),
        "debt": find_amount(["부채총계"]),
        "equity": find_amount(["자본총계"]),
    }


def get_audit_fee_raw(api_key, corp_code, year, reprt_code="11011"):
    """감사용역체결현황(adtServcCnclsSttus) 원본 응답을 DataFrame으로 반환"""
    url = "https://opendart.fss.or.kr/api/adtServcCnclsSttus.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
    }
    res = requests.get(url, params=params)
    data = res.json()
    if data.get("status") != "000":
        return None
    return pd.DataFrame(data["list"])


def pick_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def get_audit_opinion(api_key, corp_code, year, reprt_code="11011"):
    """회계감사인의 명칭 및 감사의견(accnutAdtorNmNdAdtOpinion) 조회"""
    url = "https://opendart.fss.or.kr/api/accnutAdtorNmNdAdtOpinion.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
    }
    res = requests.get(url, params=params)
    data = res.json()
    if data.get("status") != "000":
        return None
    df = pd.DataFrame(data["list"])
    return df


def main():
    corp_df = get_corp_code_map(API_KEY)
    target_df = corp_df[corp_df["corp_name"].isin(TARGET_COMPANIES)].reset_index(drop=True)

    fin_records = []
    audit_raw_samples = []
    audit_records = []

    for _, row in target_df.iterrows():
        for year in YEARS:
            # 1) 재무정보
            item = get_financial_items(API_KEY, row["corp_code"], year)
            time.sleep(0.2)
            if item is not None:
                fin_records.append({"company": row["corp_name"], "year": year, **item})

            # 2) 감사보수 원본 (컬럼명 확인용으로 최초 1건만 저장)
            audit_raw = get_audit_fee_raw(API_KEY, row["corp_code"], year)
            time.sleep(0.2)
            if audit_raw is not None and len(audit_raw_samples) < 3:
                audit_raw_samples.append(audit_raw)

            # 3) 감사의견/감사인
            opinion_df = get_audit_opinion(API_KEY, row["corp_code"], year)
            time.sleep(0.2)

            if audit_raw is not None and not audit_raw.empty:
                fee_col = pick_column(audit_raw, FEE_COL_CANDIDATES)
                hours_col = pick_column(audit_raw, HOURS_COL_CANDIDATES)
                auditor_col = pick_column(audit_raw, AUDITOR_COL_CANDIDATES)

                fee = pd.to_numeric(audit_raw[fee_col], errors="coerce").max() if fee_col else np.nan
                hours = pd.to_numeric(audit_raw[hours_col], errors="coerce").max() if hours_col else np.nan
                auditor = audit_raw[auditor_col].iloc[0] if auditor_col else None
            else:
                fee, hours, auditor = np.nan, np.nan, None

            opinion = None
            if opinion_df is not None and not opinion_df.empty and "adt_opinion" in opinion_df.columns:
                opinion = opinion_df.iloc[0]["adt_opinion"]
            if auditor is None and opinion_df is not None and not opinion_df.empty and "adtor" in opinion_df.columns:
                auditor = opinion_df.iloc[0]["adtor"]

            audit_records.append({
                "company": row["corp_name"], "year": year,
                "auditor": auditor, "audit_opinion": opinion,
                "audit_fee": fee, "audit_hours": hours,
            })

    fin_df = pd.DataFrame(fin_records)
    audit_df = pd.DataFrame(audit_records)

    fin_df.to_csv(FIN_OUTPUT_CSV, index=False, encoding="utf-8-sig")
    audit_df.to_csv(AUDIT_OUTPUT_CSV, index=False, encoding="utf-8-sig")

    if audit_raw_samples:
        pd.concat(audit_raw_samples, ignore_index=True).to_csv(
            "audit_raw_sample.csv", index=False, encoding="utf-8-sig"
        )
        print("audit_raw_sample.csv 저장 완료 — 실제 컬럼명을 확인 후 COLUMN MAPPING을 수정하세요.")

    print(f"저장 완료: {FIN_OUTPUT_CSV} ({fin_df.shape[0]}행), {AUDIT_OUTPUT_CSV} ({audit_df.shape[0]}행)")


if __name__ == "__main__":
    main()
