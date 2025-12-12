from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    list_url: str = "https://maruartcenter.co.kr/default/exhibit/exhibit01.php?sub=01"
    gallery_name: str = "마루아트센터"
    operating_hour_raw: str = "AM 10:30 ~ PM 18:30(연중무휴)"


SETTINGS = Settings()


# ==============================
# 유틸
# ==============================

def normalize_text(s: str) -> str:
    if not s:
        return ""
    return s.strip()


def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.12.3', '12.8', '8'
    """
    if not part:
        return None

    s = part.strip()

    # YYYY.MM.DD
    m = re.match(r"^\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    if base_date:
        # MM.DD
        m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s*$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

        # DD
        m = re.match(r"^\s*(\d{1,2})\s*$", s)
        if m:
            d = int(m.group(1))
            try:
                return datetime(year=base_date.year, month=base_date.month, day=d)
            except ValueError:
                return None

    return None


def parse_operating_day(operating_day: str):
    """
    '2025.12.3-12.8' -> ('2025-12-03','2025-12-08')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()
    parts = re.split(r"\s*[~-]\s*", text)
    if len(parts) != 2:
        return text, ""

    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return text, ""

    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(operating_hour: str):
    """
    'AM 10:30 ~ PM 18:30(연중무휴)' -> ('10:30', '18:30')
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


# ==============================
# 크롤러
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일: crawl()은 데이터만 반환 (DB 저장 X)
    """
    open_time, close_time = parse_operating_hour(SETTINGS.operating_hour_raw)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print(">>> [1단계] 리스트 페이지 접속")
        page.goto(SETTINGS.list_url, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []

        title_spans = page.locator("span.gallery_title")
        count = title_spans.count()
        print(f"[리스트] 발견된 전시 개수: {count}")

        for i in range(count):
            title_span = title_spans.nth(i)

            title = normalize_text(title_span.inner_text())
            if not title:
                continue

            link_el = title_span.locator("xpath=./ancestor::a[1]")
            href = (link_el.get_attribute("href") or "").strip()
            if not href:
                continue
            detail_url = urljoin(SETTINGS.list_url, href)

            title_row = title_span.locator("xpath=./ancestor::tr[1]")
            date_row = title_row.locator("xpath=./following-sibling::tr[1]")

            operating_day = ""
            if date_row.count():
                raw_date = normalize_text(date_row.inner_text())
                operating_day = (
                    raw_date.replace("[", "")
                    .replace("]", "")
                    .replace("기간 :", "")
                    .strip()
                )

            start_date, end_date = parse_operating_day(operating_day)

            exhibitions.append(
                {
                    "title": title,
                    "start_date": start_date,
                    "end_date": end_date,
                    "gallery_name": SETTINGS.gallery_name,
                    "open_time": open_time,      # ✅ 'HH:MM'
                    "close_time": close_time,    # ✅ 'HH:MM'
                    "address": "",
                    "description": "",
                    "img_url": [],
                    "_detail_url": detail_url,   # 내부용(마지막에 제거)
                }
            )

        print(f"[리스트] {len(exhibitions)}개 수집 완료. 상세 페이지 크롤링 시작...\n")

        # ==========================
        # 상세 페이지
        # ==========================
        for idx, ex in enumerate(exhibitions):
            url = ex["_detail_url"]
            print(f"[{idx+1}/{len(exhibitions)}] 상세 이동: {ex['title']}")

            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  -> 접속 실패: {e}")
                continue

            # (1) 이미지 수집
            image_urls: List[str] = []

            imgs = page.locator("#post_area img")
            if imgs.count() == 0:
                imgs = page.locator("div[style*='text-align: center'] img")

            for j in range(imgs.count()):
                src = (imgs.nth(j).get_attribute("src") or "").strip()
                if not src:
                    continue
                if "u_image" not in src:
                    continue
                image_urls.append(urljoin(url, src))

            ex["img_url"] = list(dict.fromkeys(image_urls))
            print(f"  -> 이미지: {len(ex['img_url'])}개")

            # (2) 텍스트 분석
            post_area = page.locator("#post_area")
            if post_area.count():
                texts = post_area.locator("p, div").all_inner_texts()
            else:
                texts = page.locator("body").all_inner_texts()

            location_text = SETTINGS.gallery_name
            desc_lines: List[str] = []
            is_note = False

            for t in texts:
                clean = normalize_text(t)
                if not clean:
                    continue

                # 주소/장소 후보
                if ("마루아트센터" in clean or "관" in clean) and not is_note:
                    if len(clean) < 50:
                        location_text = clean

                # 작가노트/작품설명 시작
                if any(k in clean for k in ["[작가노트]", "[작품설명]", "[작품 설명]"]):
                    is_note = True
                    continue

                if is_note:
                    desc_lines.append(clean)

            ex["address"] = location_text
            ex["description"] = "\n".join(desc_lines).strip()

        browser.close()

        # 내부키 제거
        for ex in exhibitions:
            ex.pop("_detail_url", None)

        return exhibitions


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "maruArtCenter.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
