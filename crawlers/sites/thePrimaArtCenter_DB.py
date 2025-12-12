from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, TypedDict
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    list_url: str = "https://primaartcenter.co.kr/kor/exhibition/list.html?state=current"
    gallery_name: str = "더프리마아트센터"
    default_address: str = "서울 종로구 인사동길 37-11 더프리마아트센터"
    operating_hour: str = "10:30 ~ 19:30 (입장마감 19:00)"
    timeout_ms: int = 60_000
    wait_ms: int = 1500


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

def normalize_text(s: Optional[str]) -> str:
    return s.strip() if s else ""


def uniq_keep_order(items: List[str]) -> List[str]:
    return list(dict.fromkeys([x for x in items if x]))


def safe_text(page_or_locator, selector: str) -> str:
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


def parse_operating_day(text: str) -> Tuple[str, str]:
    """
    프리마 우선 패턴:
    - '2025-08-25 - 2026-05-31'
    그 외:
    - dot 기반 / MM.DD / DD 등
    """
    if not text:
        return "", ""

    s = text.strip()

    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", s)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            pass

    parts = re.split(r"\s*[-~–]\s*", s, maxsplit=1)
    if len(parts) != 2:
        return s, ""

    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return s, ""

    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(text: str) -> Tuple[str, str]:
    """
    '10:30 ~ 19:30 (입장마감 19:00)' -> ('10:30', '19:30')
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


def cut_footer_lines(text: str) -> str:
    """
    상세 설명에서 푸터/연락처 류가 섞일 때 아래부터 컷
    """
    if not text:
        return ""

    footer_prefixes = (
        "더프리마아트센터",
        "서울특별시 종로구 인사동길",
        "서울 종로구 인사동길",
        "TEL",
        "COPYRIGHT",
    )

    kept: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(s.startswith(prefix) for prefix in footer_prefixes):
            break
        kept.append(s)

    return "\n".join(kept).strip()


# ==============================
# 크롤러 (리스트/상세 분리)
# ==============================

def _crawl_list(page: Page) -> List[_ListItem]:
    page.goto(SETTINGS.list_url, timeout=SETTINGS.timeout_ms)
    page.wait_for_timeout(SETTINGS.wait_ms)

    items = page.locator("div.item_wrap.item_wrap_02.hover_line_action")
    count = items.count()
    print(f"[리스트] 전시 개수(프리마): {count}")

    results: List[_ListItem] = []
    seen: set[str] = set()

    open_time, close_time = parse_operating_hour(SETTINGS.operating_hour)

    for i in range(count):
        item = items.nth(i)

        href = safe_attr(item, "a.btn", "href")
        if not href:
            continue

        detail_url = urljoin(SETTINGS.list_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        title = safe_text(item, "div.info-book dl dt")
        if not title:
            # fallback: 링크 텍스트
            title = safe_text(item, "a.btn")
        title = " ".join(title.split())
        if not title:
            continue

        operating_day = safe_text(item, "div.info-book dl dd")
        start_date, end_date = parse_operating_day(operating_day)

        # 썸네일
        thumb_urls: List[str] = []
        thumb_src = safe_attr(item, ".img_wrap img", "src")
        if thumb_src:
            thumb_urls.append(urljoin(SETTINGS.list_url, thumb_src))

        ex: Exhibition = {
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "address": SETTINGS.default_address,  # 상세에서 전시장소 있으면 덮어씀
            "gallery_name": SETTINGS.gallery_name,
            "open_time": open_time,
            "close_time": close_time,
            "author": "",
            "description": "",
            "img_url": thumb_urls,
        }

        results.append({"ex": ex, "detail_url": detail_url})

    print(f"[리스트] 수집된 전시 수: {len(results)}")
    return results


def _extract_hall_detail(page: Page) -> str:
    """
    상세 상단 정보에서 '전시장소'의 dd 텍스트 추출
    """
    info_items = page.locator("ul li")
    for i in range(info_items.count()):
        li = info_items.nth(i)
        dt = safe_text(li, "dt")
        dd = safe_text(li, "dd")
        if dt and dd and "전시장소" in dt:
            return dd
    return ""


def _extract_author(page: Page) -> str:
    """
    테이블 tr에서 '작가' 라벨 탐색
    """
    rows = page.locator("tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("th, td")
        if cells.count() < 2:
            continue
        label = normalize_text(cells.nth(0).inner_text())
        if "작가" in label:
            return "".join(normalize_text(cells.nth(1).inner_text()).split())
    return ""


def _extract_description(page: Page) -> str:
    """
    우선순위:
    1) div.detail.bar
    2) div.exhibition_view, div.view, div.view_cont ...
    3) article
    """
    content = page.locator("div.detail.bar")
    if not content.count():
        content = page.locator("div.exhibition_view, div.view, div.view_cont, article")
    if not content.count():
        content = page.locator("body")

    container = content.first
    p_texts = container.locator("p").all_inner_texts()
    if not p_texts:
        raw = normalize_text(container.inner_text())
        return cut_footer_lines(raw) or raw

    raw = "\n".join([normalize_text(t) for t in p_texts if normalize_text(t)])
    trimmed = cut_footer_lines(raw)
    return trimmed or raw


def _extract_images(page: Page, base_url: str) -> List[str]:
    """
    - 썸네일 + 상세 이미지 합치기 전제
    - 상세는 'upload/board' 포함만 수집 (프리마 이미지 경로 필터)
    """
    urls: List[str] = []
    imgs = page.locator("img")
    for i in range(imgs.count()):
        src = (imgs.nth(i).get_attribute("src") or "").strip()
        if not src:
            continue
        if "upload/board" not in src:
            continue
        urls.append(urljoin(base_url, src))
    return uniq_keep_order(urls)


def _enrich_detail(page: Page, item: _ListItem) -> None:
    ex = item["ex"]
    url = item["detail_url"]

    print(f"[상세] 이동: {ex['title']} -> {url}")
    try:
        page.goto(url, timeout=SETTINGS.timeout_ms)
        page.wait_for_timeout(SETTINGS.wait_ms)
    except Exception as e:
        print(f"[상세] 접속 실패: {e}")
        return

    hall_detail = _extract_hall_detail(page)
    if hall_detail:
        ex["address"] = hall_detail

    ex["author"] = _extract_author(page)
    ex["description"] = _extract_description(page)

    detail_images = _extract_images(page, url)
    ex["img_url"] = uniq_keep_order((ex.get("img_url") or []) + detail_images)

    print(f"[상세] 이미지: {len(ex['img_url'])}개, 설명 길이: {len(ex['description'])}")


# ==============================
# 공개 API
# ==============================

def crawl() -> List[Exhibition]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        items = _crawl_list(page)
        for item in items:
            _enrich_detail(page, item)

        browser.close()
        print(f"[최종] 프리마 전시 {len(items)}개 수집 완료")
        return [it["ex"] for it in items]


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "thePrimaArtCenter.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
