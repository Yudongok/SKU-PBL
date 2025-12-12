from __future__ import annotations

from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any


# ==============================
# 기본 설정
# ==============================

LIST_URL = "https://galleryinsaart.com/exhibitions-current/"
GALLERY_NAME = "갤러리인사아트"
DEFAULT_OPENING_HOURS_RAW = "AM 10:00 ~ PM 19:00"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part 예시: '2025.12.3', '2025 11/26', '12/08', '11. 26' 등
    base_date: 연도가 없을 때 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()

    # "11/26" -> "11.26", "2025 11/26" -> "2025.11.26"
    s = s.replace("/", ".")
    s = re.sub(r"\s+", ".", s)
    s = re.sub(r"\.+", ".", s)

    # 1) YYYY.MM.DD
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # 2) MM.DD (연도는 base_date 기준)
    if base_date:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

    # 3) DD (연/월은 base_date 기준)
    if base_date:
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
    '2025 11/26 - 12/08' 처럼 슬래시나 공백이 섞여도 처리
    성공 시: ('YYYY-MM-DD', 'YYYY-MM-DD')
    실패 시: (원본 문자열 또는 '', '')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # -, ~, – 등의 구분자로 시작/종료일 분리
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
    예시: 'AM 10:00 ~ PM 19:00' 또는 '10:00 ~ 18:00'
    -> ('10:00', '19:00')
    """
    if not operating_hour:
        return "", ""

    base = operating_hour.split("(", 1)[0].strip()
    times = re.findall(r"\d{1,2}:\d{2}", base)

    if len(times) >= 2:
        return times[0], times[1]
    if len(times) == 1:
        return times[0], ""
    return "", ""


def normalize_text(s: str) -> str:
    """None/공백/줄바꿈 정리해서 '의미 있는 텍스트'만 남김"""
    if not s:
        return ""
    return s.strip()


# ==============================
# 크롤러 본체 (갤러리 인사아트)
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일:
    - 이 파일은 '크롤링만' 해서 list[dict] 반환
    - DB 저장은 runner/common db 모듈에서 처리
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 현재전시 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []
        detail_urls: List[str] = []

        # ▶ 전시 제목(h4 안의 a) 기준으로 목록 수집
        h4_links = page.locator("h4 a")
        count = h4_links.count()
        print(f"[리스트] 전시 개수(h4 a): {count}")

        for i in range(count):
            link = h4_links.nth(i)

            title_kr = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # 전시장(h3): 해당 h4 위쪽의 가장 가까운 h3
            h4 = link.locator("xpath=ancestor::h4[1]")
            section_loc = h4.locator("xpath=preceding::h3[1]")
            section = section_loc.inner_text().strip() if section_loc.count() else ""

            # 전시 기간 (p[2])
            date_loc = h4.locator("xpath=following-sibling::p[2]")
            date_text = date_loc.inner_text().strip() if date_loc.count() else ""

            # 운영 시간 (고정)
            open_time, close_time = parse_operating_hour(DEFAULT_OPENING_HOURS_RAW)

            # 날짜 파싱
            start_date, end_date = parse_operating_day(date_text)

            exhibitions.append(
                {
                    "address": section,
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": open_time,
                    "close_time": close_time,
                    "gallery_name": GALLERY_NAME,
                    "author": "",
                    "description": "",
                    "img_url": [],
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) 상세 페이지 크롤링
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # 작가 정보: 첫 h5/h6
            artist = ""
            h5s = page.locator("h5, h6")
            if h5s.count() >= 1:
                artist = h5s.nth(0).inner_text().strip()

            # 설명 텍스트
            text_container = page.locator("div.fusion-text.fusion-text-2")
            if text_container.count():
                paragraphs = text_container.locator("p").all_inner_texts()
            else:
                paragraphs = page.locator("p").all_inner_texts()

            description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # 이미지 URL들 (필터: 2025 업로드만)
            img_elements = page.locator("img")
            image_urls: List[str] = []
            for idx in range(img_elements.count()):
                src = (img_elements.nth(idx).get_attribute("src") or "").strip()
                if not src:
                    continue
                if "wp-content/uploads/2025/" not in src:
                    continue
                image_urls.append(src)

            ex["author"] = artist
            ex["description"] = description
            ex["img_url"] = list(dict.fromkeys(image_urls))

            print(
                f"[상세] 이미지 개수: {len(ex['img_url'])}, "
                f"설명 길이: {len(normalize_text(description))}"
            )

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "gallery_insaArt.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
