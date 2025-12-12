from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    list_url: str = "https://www.insaartcenter.com/bbs/board.php?bo_table=exhibition_current"
    default_gallery_name: str = "인사아트센터"

    # 사이트 고정 운영시간
    operating_hour_raw: str = "AM 10:00 ~ PM 19:00(화요일 정기 휴무)"
    # 이미지 필터
    image_path_keyword: str = "/data/file/exhibition_current/"


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
    part: '2025. 11. 26', '2025.12.3', '12.8', '8' 등
    base_date: 연/월 생략 시 기준 날짜
    """
    if not part:
        return None

    s = part.strip()
    s = re.sub(r"\s*\.\s*", ".", s)

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
    '2025. 11. 26 - 2025. 12. 15'  -> ('2025-11-26', '2025-12-15')
    '2025.12.3-12.8'               -> ('2025-12-03', '2025-12-08')
    실패 시: (원본, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
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
    'AM 10:00 ~ PM 19:00(화요일 정기 휴무)' -> ('10:00','19:00')
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
    ✅ 회사 스타일:
    - 크롤링/정제만 수행하고 list[dict] 반환
    - DB 저장은 runner/공통 db 모듈에서 처리
    """
    open_time, close_time = parse_operating_hour(SETTINGS.operating_hour_raw)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(SETTINGS.list_url, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []
        detail_urls: List[str] = []

        items = page.locator("div.gall_text_href")
        count = items.count()
        print(f"[리스트] 전시 개수(div.gall_text_href): {count}")

        for i in range(count):
            item = items.nth(i)

            link = item.locator("a.bo_tit")
            if not link.count():
                continue

            title = normalize_text(link.inner_text())
            href = (link.get_attribute("href") or "").strip()
            if not href:
                continue
            detail_url = urljoin(SETTINGS.list_url, href)

            rows = item.locator(".list-spec table tr")
            row_count = rows.count()

            operating_day = normalize_text(rows.nth(0).inner_text()) if row_count > 0 else ""
            hall = normalize_text(rows.nth(1).inner_text()) if row_count > 1 else ""
            gallery_txt = normalize_text(rows.nth(2).inner_text()) if row_count > 2 else ""

            start_date, end_date = parse_operating_day(operating_day)

            exhibitions.append(
                {
                    "title": title,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": hall,
                    "gallery_name": gallery_txt or SETTINGS.default_gallery_name,
                    "open_time": open_time,
                    "close_time": close_time,
                    "author": "",
                    "description": "",
                    "img_url": [],
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # --------------------------
        # 상세 페이지
        # --------------------------
        for idx, ex in enumerate(exhibitions):
            url = detail_urls[idx]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")

            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 작가
            artist = ""
            spec = page.locator("div.spec")
            if spec.count():
                rows = spec.locator("table tr")
                for j in range(rows.count()):
                    th = rows.nth(j).locator("th")
                    if not th.count():
                        continue
                    label = normalize_text(th.inner_text())
                    if label == "작가":
                        td = rows.nth(j).locator("td")
                        if td.count():
                            artist = normalize_text(td.inner_text()).replace(" ", "")
                        break

            # (2) 설명
            content = page.locator("#bo_v_con")
            if not content.count():
                content = page.locator(".bo_v_con")

            paragraphs: List[str] = []
            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = [normalize_text(t) for t in p_loc.all_inner_texts() if normalize_text(t)]
                else:
                    raw = normalize_text(content.inner_text())
                    if raw:
                        paragraphs = [raw]
            else:
                paragraphs = [normalize_text(t) for t in page.locator("p").all_inner_texts() if normalize_text(t)]

            description = "\n".join(paragraphs).strip()

            # (3) 이미지
            image_urls: List[str] = []
            gallery_items = page.locator("#img-gallery li")
            for k in range(gallery_items.count()):
                li = gallery_items.nth(k)

                src = li.get_attribute("data-src")
                if not src:
                    img_el = li.locator("img")
                    if img_el.count():
                        src = img_el.first.get_attribute("src")

                src = (src or "").strip()
                if not src:
                    continue

                if SETTINGS.image_path_keyword not in src:
                    continue

                image_urls.append(src)

            image_urls = list(dict.fromkeys(image_urls))

            ex["author"] = artist
            ex["description"] = description
            ex["img_url"] = image_urls

            print(f"[상세] 이미지: {len(image_urls)}개 / 설명 길이: {len(description)}")

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

        out_path = json_dir / "insaArt.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
