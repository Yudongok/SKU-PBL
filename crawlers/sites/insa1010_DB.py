from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from openai import OpenAI


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    list_url: str = "https://www.insa1010.com/28"
    gallery_name: str = "인사1010"

    # 사이트 운영시간이 고정이면 여기에서 관리
    default_open_time: str = "11:00"
    default_close_time: str = "19:00"

    # 이미지 필터
    img_prefix: str = "https://cdn.imweb.me/upload/"

    # gpt 모델
    gpt_model: str = "gpt-4o-mini"


SETTINGS = Settings()


# ==============================
# 유틸
# ==============================

def normalize_text(s: str) -> str:
    if not s:
        return ""
    return s.strip()


def parse_single_date(part: str, base_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    입력 예:
      - '2025.12.3'
      - '2025-12-03'
      - '2025년 12월 3일'
      - '12.8'
      - '12월 8일'
      - '8'
    """
    if not part:
        return None

    s = part.strip()

    # 한글 날짜 -> 점(.) 형태로 정규화
    s = re.sub(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?", r"\1.\2.\3", s)
    s = re.sub(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일?", r"\1.\2", s)

    # -, / -> .
    s = s.replace("-", ".").replace("/", ".")

    # 점 주변 공백 / 중복 점 정리
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\.+", ".", s)
    s = s.strip(" .")

    # YYYY.MM.DD
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    if base_date:
        # MM.DD
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

        # DD
        m = re.match(r"^(\d{1,2})$", s)
        if m:
            d = int(m.group(1))
            try:
                return datetime(year=base_date.year, month=base_date.month, day=d)
            except ValueError:
                return None

    return None


def parse_operating_day(operating_day: str):
    """
    예:
      '전시 기간: 2025. 11. 26 - 2025. 12. 15 (월요일 휴관)'
      '2025.12.3-12.8'
      '2025-12-03 ~ 2025-12-08'
      '2025년 12월 3일 ~ 12월 8일'
    -> ('YYYY-MM-DD', 'YYYY-MM-DD')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 첫 숫자부터 자르기 (앞에 '전시 기간:' 같은 문자열 제거)
    m = re.search(r"\d", text)
    if not m:
        return text, ""
    text = text[m.start():]

    # 괄호 제거
    text = re.sub(r"\(.*?\)", "", text).strip()

    # 구분자로 split
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        dt = parse_single_date(text)
        if dt:
            return dt.strftime("%Y-%m-%d"), ""
        return text, ""

    start_part, end_part = parts[0], parts[1]

    start_dt = parse_single_date(start_part)
    if not start_dt:
        return text, ""

    end_dt = parse_single_date(end_part, base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(operating_hour: str):
    """
    예:
      '10:00 ~ 18:00'
      '10:00-18:00'
      '10:00 – 18:00(월요일 휴관)'
    -> ('10:00','18:00')
    """
    if not operating_hour:
        return "", ""

    base = operating_hour.split("(", 1)[0].strip()
    parts = re.split(r"\s*[-~–]\s*", base)
    if len(parts) != 2:
        return base.strip(), ""
    return parts[0].strip(), parts[1].strip()


# ==============================
# OpenAI (GPT)
# ==============================

SYSTEM_PROMPT = """
당신은 전시 정보 정리 도우미입니다.
입력으로 전시 소개 텍스트와 이미지 URL 목록이 주어집니다.
이 정보를 보고 아래 형식의 JSON만 순수 텍스트로 출력하세요.

{
  "title": "...",
  "description": "...",
  "imageUrl": "...",
  "operatingHour": "...",
  "operatingDay": "...",
  "author": "..."
}

규칙:
- title: 전시 제목으로 자연스럽게 한 줄.
- description: 소개/설명 텍스트. 한국어로 자연스럽게.
- imageUrl: 주어진 imageUrls 중에서 가장 대표 이미지 1개. 없다면 "".
- operatingHour: 관람 가능 시간 (예: "10:00 ~ 18:00").
- operatingDay: 전시 기간은 반드시 'YYYY.MM.DD ~ YYYY.MM.DD' 형식.
- 필요하면 휴관 정보는 괄호로 추가 가능.
- 반드시 유효한 JSON만 출력.
- 작가를 추출해서 author에 넣고 없으면 "".
""".strip()


def get_openai_client() -> OpenAI:
    # .env 로드 (runner에서 여러 파일 실행해도 한 번만 로드돼도 괜찮음)
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. (.env 또는 환경변수 확인)")
    return OpenAI(api_key=api_key)


def extract_fields_with_gpt(client: OpenAI, description_text: str, image_urls: List[str]) -> Dict[str, str]:
    user_payload = {"description": description_text, "imageUrls": image_urls}

    resp = client.chat.completions.create(
        model=SETTINGS.gpt_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        temperature=0.2,
    )

    raw = (resp.choices[0].message.content or "").strip()

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("GPT JSON이 dict가 아님")
    except Exception:
        # 실패하면 최소한의 fallback
        print("⚠ GPT 응답 JSON 파싱 실패. fallback 사용")
        data = {
            "title": "",
            "description": description_text,
            "imageUrl": image_urls[0] if image_urls else "",
            "operatingHour": "",
            "operatingDay": "",
            "author": "",
        }

    # 키 보정
    return {
        "title": str(data.get("title", "") or ""),
        "description": str(data.get("description", description_text) or description_text),
        "imageUrl": str(data.get("imageUrl", image_urls[0] if image_urls else "") or ""),
        "operatingHour": str(data.get("operatingHour", "") or ""),
        "operatingDay": str(data.get("operatingDay", "") or ""),
        "author": str(data.get("author", "") or ""),
    }


# ==============================
# 크롤러
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일:
    - 크롤링 + 정제만 수행하고 list[dict] 반환
    - DB 저장은 runner/공통 모듈에서 처리
    """
    client = get_openai_client()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(SETTINGS.list_url, timeout=60_000)
        page.wait_for_timeout(3000)

        detail_urls: List[str] = []
        seen = set()

        links = page.locator("a[href*='bmode=view']")
        link_count = links.count()
        print(f"[리스트] 전시 상세 링크 개수: {link_count}")

        for i in range(link_count):
            href = (links.nth(i).get_attribute("href") or "").strip()
            if not href:
                continue

            detail_url = urljoin(SETTINGS.list_url, href.split("#")[0])
            if detail_url in seen:
                continue
            seen.add(detail_url)
            detail_urls.append(detail_url)

        print(f"[리스트] 중복 제거 후 전시 수: {len(detail_urls)}")

        exhibitions: List[Dict[str, Any]] = []

        for url in detail_urls:
            print(f"\n[상세] 이동: {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(2000)

            # 설명 텍스트
            text_container = page.locator("div.fusion-text.fusion-text-2")
            if text_container.count() > 0:
                paragraphs = text_container.locator("p").all_inner_texts()
            else:
                paragraphs = page.locator("p").all_inner_texts()

            cleaned = [t.strip(" ﻿\u200b") for t in paragraphs if t.strip(" ﻿\u200b")]
            description_text = "\n".join(cleaned)

            # 이미지 수집
            image_urls: List[str] = []
            img_elements = page.locator("img")
            for idx in range(img_elements.count()):
                src = (img_elements.nth(idx).get_attribute("src") or "").strip()
                if not src:
                    continue
                if not src.startswith(SETTINGS.img_prefix):
                    continue
                image_urls.append(src)

            image_urls = list(dict.fromkeys(image_urls))

            # GPT 추출
            gpt_data = extract_fields_with_gpt(client, description_text, image_urls)

            start_date, end_date = parse_operating_day(gpt_data["operatingDay"])
            open_time, close_time = parse_operating_hour(gpt_data["operatingHour"])

            # 사이트 고정 운영시간을 우선한다면 아래처럼 override
            # (현재 원본 코드처럼 고정 운영시간을 쓰고 싶으면 유지)
            open_time = SETTINGS.default_open_time
            close_time = SETTINGS.default_close_time

            exhibitions.append(
                {
                    "title": normalize_text(gpt_data["title"]),
                    "description": normalize_text(gpt_data["description"]),
                    "author": normalize_text(gpt_data["author"]),
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": open_time,
                    "close_time": close_time,
                    "img_url": image_urls,
                    "gallery_name": SETTINGS.gallery_name,
                    "address": None,
                }
            )

            print(f"[상세] 제목: {gpt_data['title']}")
            print(f"[상세] 기간: {start_date} ~ {end_date}")
            print(f"[상세] 이미지: {len(image_urls)}개")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 수집 완료")
        return exhibitions


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "insa1010.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
