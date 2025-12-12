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
    list_url: str = "https://rhogallery.com/ko/current/"
    gallery_name: str = "노화랑"
    base_address: str = "서울 종로구 인사동길 54 노화랑"
    open_time: str = "10:00"
    close_time: str = "18:00"


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
    '2025. 11. 21', '2025.11.21.', '12. 10', '8', '2025-08-25'
    """
    if not part:
        return None

    s = part.strip().rstrip(".")  # 끝 점 제거

    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # "2025. 11. 21" -> "2025.11.21"
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
    '2025. 11. 21 – 12. 10'
    '2025.11.21-2025.12.10'
    '2025-11-21 ~ 2025-12-10'
    -> ('YYYY-MM-DD','YYYY-MM-DD')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # YYYY-MM-DD 두 개면 우선 처리
    found = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if len(found) >= 2:
        dt1 = parse_single_date(found[0])
        dt2 = parse_single_date(found[1])
        if dt1 and dt2:
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")

    # -, ~, – 기준 분리
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        return text, ""

    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return text, ""

    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


# ==============================
# 크롤러
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일: crawl()은 크롤링만 수행하고 데이터 반환 (DB 저장 X)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(SETTINGS.list_url, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []

        items = page.locator("article.category-00_current-exhibition")
        count = items.count()
        print(f"[리스트] 전시 개수(rhogallery): {count}")

        if count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. 셀렉터를 다시 확인해 주세요.")
            browser.close()
            return []

        seen_urls: set[str] = set()

        # --------------------------
        # 리스트 수집
        # --------------------------
        for i in range(count):
            item = items.nth(i)

            # 상세 URL: 썸네일 링크 우선
            link = item.locator("a.post-thumbnail-rollover")
            if link.count():
                href = link.first.get_attribute("href") or ""
            else:
                href_el = item.locator("h3.entry-title a")
                href = href_el.first.get_attribute("href") if href_el.count() else ""

            href = (href or "").strip()
            if not href:
                continue

            detail_url = urljoin(SETTINGS.list_url, href)
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            # 제목
            title_el = item.locator("h3.entry-title")
            title = normalize_text(title_el.inner_text() if title_el.count() else item.inner_text())
            if not title:
                continue

            # 작가: 기존 로직 유지(제목=작가로 가정)
            author = title

            # 기간
            date_el = item.locator(".entry-excerpt p")
            operating_day = normalize_text(date_el.inner_text() if date_el.count() else "")
            start_date, end_date = parse_operating_day(operating_day)

            exhibitions.append(
                {
                    "title": title,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": SETTINGS.base_address,
                    "gallery_name": SETTINGS.gallery_name,
                    "open_time": SETTINGS.open_time,      # ✅ 'HH:MM'
                    "close_time": SETTINGS.close_time,    # ✅ 'HH:MM'
                    "author": author,
                    "description": "",
                    "img_url": [],  # 상세에서 채움
                    "_detail_url": detail_url,  # 내부용
                }
            )

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # --------------------------
        # 상세 페이지 수집
        # --------------------------
        for idx, ex in enumerate(exhibitions):
            url = ex["_detail_url"]
            print(f"\n[상세 {idx+1}/{len(exhibitions)}] 이동: {ex['title']} -> {url}")

            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 작가 정보 테이블이 있으면 갱신
            author = ex["author"]
            rows = page.locator("tr")
            for j in range(rows.count()):
                row = rows.nth(j)
                cells = row.locator("th, td")
                if cells.count() < 2:
                    continue
                label = normalize_text(cells.nth(0).inner_text())
                if "작가" in label or "Artist" in label:
                    author = normalize_text(cells.nth(1).inner_text()).replace(" ", "")
                    break

            # (2) 설명: wrapper 중 가장 긴 블럭 선택
            wrappers = page.locator("div.wpb_text_column.wpb_content_element > div.wpb_wrapper")
            chosen = None
            max_len = 0

            for w_i in range(wrappers.count()):
                w = wrappers.nth(w_i)
                txt = normalize_text(w.inner_text())
                if len(txt) > max_len:
                    max_len = len(txt)
                    chosen = w

            paragraphs: List[str] = []
            if chosen:
                p_loc = chosen.locator("p")
                if p_loc.count():
                    for k in range(p_loc.count()):
                        t = normalize_text(p_loc.nth(k).inner_text())
                        if t:
                            paragraphs.append(t)
                else:
                    t = normalize_text(chosen.inner_text())
                    if t:
                        paragraphs.append(t)

            description = "\n".join(paragraphs).strip()

            # (3) 이미지: vc_column-inner 내부에서 uploads 링크/이미지
            image_urls: List[str] = []

            link_els = page.locator("div.vc_column-inner a[href*='wp-content/uploads']")
            for k in range(link_els.count()):
                href = (link_els.nth(k).get_attribute("href") or "").strip()
                if not href:
                    continue
                image_urls.append(urljoin(url, href))

            img_els = page.locator("div.vc_column-inner img[src*='wp-content/uploads']")
            for k in range(img_els.count()):
                src = (img_els.nth(k).get_attribute("src") or "").strip()
                if not src:
                    continue
                image_urls.append(urljoin(url, src))

            image_urls = list(dict.fromkeys(image_urls))

            ex["author"] = author
            ex["description"] = description
            ex["img_url"] = image_urls

            print(f"[상세] 이미지 {len(image_urls)}개 / 설명 길이 {len(description)}")

        browser.close()

        # 내부용 키 제거
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

        out_path = json_dir / "roGallery.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
