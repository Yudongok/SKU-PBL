from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, TypedDict
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    list_url: str = "https://www.sungallery.co.kr/exhibitions/current/"
    gallery_name: str = "선화랑"
    gallery_address: str = "서울 종로구 인사동5길 8 선화랑"
    default_open_time_str: str = "10:00"
    default_close_time_str: str = "18:00"
    timeout_ms: int = 60_000


SETTINGS = Settings()


# ==============================
# 타입
# ==============================

class Exhibition(TypedDict):
    title: str
    description: str
    address: str
    author: str
    start_date: str
    end_date: str
    open_time: str
    close_time: str
    img_url: List[str]
    gallery_name: str


class _ListItem(TypedDict):
    ex: Exhibition
    detail_url: str


# ==============================
# 유틸
# ==============================

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def normalize_text(s: Optional[str]) -> str:
    return s.strip() if s else ""


def uniq_keep_order(items: List[str]) -> List[str]:
    # 빈 문자열 제거 + 순서 유지 중복 제거
    return list(dict.fromkeys([x for x in items if x]))


def month_str_to_int(mon: str) -> Optional[int]:
    if not mon:
        return None
    key = mon.strip()[:3].upper()
    return _MONTH_MAP.get(key)


def safe_text(page_or_locator, selector: str) -> str:
    """
    page_or_locator.locator(selector).first.inner_text() 안전 추출
    """
    loc = page_or_locator.locator(selector)
    if not loc.count():
        return ""
    try:
        return normalize_text(loc.first.inner_text())
    except Exception:
        return ""


def safe_attr(page_or_locator, selector: str, attr: str) -> str:
    loc = page_or_locator.locator(selector)
    if not loc.count():
        return ""
    try:
        return normalize_text(loc.first.get_attribute(attr) or "")
    except Exception:
        return ""


def parse_operating_hour(text: str) -> Tuple[str, str]:
    """
    '10:00 ~ 18:00' -> ('10:00', '18:00')
    """
    if not text:
        return "", ""
    base = text.split("(", 1)[0].strip()
    times = re.findall(r"\d{1,2}:\d{2}", base)
    if len(times) >= 2:
        return times[0], times[1]
    if len(times) == 1:
        return times[0], ""
    return "", ""


def parse_single_date(part: str, base_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    지원:
    - YYYY-MM-DD
    - YYYY.MM.DD
    - MM.DD (base_date.year)
    - DD (base_date.year/base_date.month)
    """
    if not part:
        return None

    s = part.strip()

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(y, mth, d)
        except ValueError:
            return None

    s = re.sub(r"\s*\.\s*", ".", s)
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(y, mth, d)
        except ValueError:
            return None

    if base_date:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(base_date.year, mth, d)
            except ValueError:
                return None

        m = re.match(r"^(\d{1,2})$", s)
        if m:
            d = int(m.group(1))
            try:
                return datetime(base_date.year, base_date.month, d)
            except ValueError:
                return None

    return None


def parse_operating_day(operating_day: str) -> Tuple[str, str]:
    """
    지원:
    1) '3 Dec 2025 - 13 Jan 2026'
    2) '3 - 31 Dec 2025'
    3) '2025-12-03 - 2025-12-08'
    4) fallback: '.' 기반 분해
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 1) "3 Dec 2025 - 13 Jan 2026"
    m = re.match(
        r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})\s*[-–]\s*"
        r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$",
        text,
    )
    if m:
        d1, mon1, y1, d2, mon2, y2 = m.groups()
        m1, m2 = month_str_to_int(mon1), month_str_to_int(mon2)
        if m1 and m2:
            try:
                dt1 = datetime(int(y1), m1, int(d1))
                dt2 = datetime(int(y2), m2, int(d2))
                return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2) "3 - 31 Dec 2025"
    m = re.match(r"^(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$", text)
    if m:
        d1, d2, mon, y = m.groups()
        mth = month_str_to_int(mon)
        if mth:
            try:
                dt1 = datetime(int(y), mth, int(d1))
                dt2 = datetime(int(y), mth, int(d2))
                return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3) YYYY-MM-DD - YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", text)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 4) fallback
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        return "", ""

    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return "", ""

    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def is_navigation_page(url: str) -> bool:
    clean = url.rstrip("/")
    return clean.endswith("/exhibitions/current") or clean.endswith("/exhibitions/past") or clean.endswith("/exhibitions")


def pick_korean_description(page: Page) -> str:
    """
    상세에서 한글 포함 문단만 모아서 description 생성
    """
    selectors = ["div.prose .description", "div.exhibition-detail", "div.body"]
    container = None
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count():
            container = loc.first
            break
    if not container:
        return ""

    p_texts = container.locator("p").all_inner_texts()
    paragraphs: List[str] = []

    if not p_texts:
        raw = normalize_text(container.inner_text())
        if raw and re.search(r"[가-힣]", raw):
            paragraphs.append(raw)
    else:
        for t in p_texts:
            t = normalize_text(t)
            if t and re.search(r"[가-힣]", t):
                paragraphs.append(t)

    return "\n\n".join(paragraphs).strip()


def collect_images(page: Page, base_url: str) -> List[str]:
    """
    전시 상세 컨텐츠 영역을 우선으로 이미지 수집.
    - cloudinary(artlogic-res...) + /images/exhibitions/ 는 그대로
    - 그 외는 urljoin
    """
    # 우선순위 컨테이너
    containers = ["main", "article", "div.exhibition-detail", "div.body"]
    scope = None
    for sel in containers:
        loc = page.locator(sel)
        if loc.count():
            scope = loc.first
            break

    img_scope = scope if scope else page
    img_els = img_scope.locator("img")

    urls: List[str] = []
    for i in range(img_els.count()):
        src = img_els.nth(i).get_attribute("src") or ""
        src = src.strip()
        if not src:
            continue

        if "artlogic-res.cloudinary.com" in src and "/images/exhibitions/" in src:
            urls.append(src)
        else:
            urls.append(urljoin(base_url, src))

    return uniq_keep_order(urls)


# ==============================
# 크롤러
# ==============================

def _crawl_list(page: Page) -> List[_ListItem]:
    page.goto(SETTINGS.list_url, timeout=SETTINGS.timeout_ms)
    page.wait_for_timeout(1500)

    items = page.locator("ul.clearwithin > li")
    count = items.count()
    print(f"[리스트] 항목 개수(선화랑): {count}")

    seen: set[str] = set()
    results: List[_ListItem] = []

    for i in range(count):
        item = items.nth(i)
        href = safe_attr(item, "a", "href")
        if not href:
            continue

        detail_url = urljoin(SETTINGS.list_url, href)
        if is_navigation_page(detail_url) or detail_url in seen:
            continue
        seen.add(detail_url)

        title = safe_text(item, "div.content h2")
        if not title:
            continue

        author = safe_text(item, "div.content span.subtitle")
        date_text = safe_text(item, "div.content span.date")
        start_date, end_date = parse_operating_day(date_text)

        short_desc = safe_text(item, "div.content span.description")

        # 썸네일
        img_url = ""
        thumb = item.locator("span.image img")
        if thumb.count():
            src = (thumb.first.get_attribute("data-src") or thumb.first.get_attribute("src") or "").strip()
            if src:
                img_url = src if src.startswith("http") else urljoin(detail_url, src)

        operating_hour = f"{SETTINGS.default_open_time_str} ~ {SETTINGS.default_close_time_str}"
        open_time_str, close_time_str = parse_operating_hour(operating_hour)

        ex: Exhibition = {
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "address": SETTINGS.gallery_address,
            "gallery_name": SETTINGS.gallery_name,
            "open_time": open_time_str,
            "close_time": close_time_str,
            "author": author,
            "description": short_desc,  # 상세에서 더 긴 한글 설명 있으면 교체
            "img_url": [img_url] if img_url else [],
        }

        results.append({"ex": ex, "detail_url": detail_url})

    print(f"[리스트] 최종 수집된 전시 수: {len(results)}")
    return results


def _enrich_detail(page: Page, item: _ListItem) -> None:
    ex = item["ex"]
    url = item["detail_url"]

    print(f"[상세] 이동: {ex['title']} -> {url}")
    try:
        page.goto(url, timeout=SETTINGS.timeout_ms)
        page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[상세] 접속 실패: {e}")
        return

    # 더 긴 한글 설명이 있으면 교체
    detailed_desc = pick_korean_description(page)
    if detailed_desc and len(detailed_desc) > len(ex.get("description", "")):
        ex["description"] = detailed_desc

    # 이미지 합치기
    detail_images = collect_images(page, url)
    ex["img_url"] = uniq_keep_order((ex.get("img_url") or []) + detail_images)


def crawl() -> List[Exhibition]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        items = _crawl_list(page)
        for item in items:
            _enrich_detail(page, item)

        browser.close()
        return [it["ex"] for it in items]


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "sunGallery.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
