from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, TypedDict
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright


# ==============================
# 설정
# ==============================

@dataclass(frozen=True)
class Settings:
    base_url: str = "http://tongingallery.com/exhibitions"
    gallery_name: str = "통인화랑"
    open_time: str = "10:30"
    close_time: str = "18:30"
    timeout_ms: int = 60_000
    wait_ms: int = 1200
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/115.0.0.0 Safari/537.36"
    )


SETTINGS = Settings()


# ==============================
# 타입
# ==============================

class Exhibition(TypedDict):
    title: str
    address: str
    start_date: str
    end_date: str
    open_time: str
    close_time: str
    gallery_name: str
    author: str
    description: str
    img_url: List[str]


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


def parse_single_date(part: str, base_date: Optional[datetime] = None) -> Optional[datetime]:
    if not part:
        return None

    s = re.sub(r"\s*\.\s*", ".", part.strip())

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


def parse_operating_day(operating_day: str) -> Tuple[str, str]:
    if not operating_day:
        return "", ""
    text = operating_day.strip()

    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        return operating_day, ""

    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return operating_day, ""

    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def _page_end_and_wait(page: Page) -> None:
    """
    이미지/갤러리 lazyload 대응용: End로 스크롤 후 잠깐 대기
    """
    try:
        page.keyboard.press("End")
    except Exception:
        pass
    page.wait_for_timeout(SETTINGS.wait_ms)


# ==============================
# 리스트 수집 (ON VIEW / UPCOMING 공통)
# ==============================

def _collect_section(page: Page, header_text: str, mode: str) -> List[_ListItem]:
    """
    mode:
      - "onview": 기존 코드처럼 header_row 기준 sibling row를 사용
      - "upcoming": inside 컨테이너 안에서 수집
    """
    header = page.locator("h6", has_text=header_text).first
    if not header.count():
        return []

    items: List[_ListItem] = []

    if mode == "onview":
        header_row = header.locator("xpath=ancestor::div[contains(@class,'doz_row')][1]")
        image_row = header_row.locator("xpath=following-sibling::div[contains(@class,'doz_row')][2]")
        text_row = header_row.locator("xpath=following-sibling::div[contains(@class,'doz_row')][3]")

        containers = text_row.locator("div.text-table")
        links = image_row.locator("a._fade_link")
        thumbs = image_row.locator("img.org_image")

    else:  # "upcoming"
        inside = header.locator("xpath=ancestor::div[contains(@class,'inside')][1]")
        containers = inside.locator("div.text-table")
        links = inside.locator("a._fade_link")
        thumbs = inside.locator("img.org_image")

    cnt = containers.count()
    print(f"[{header_text}] {cnt}개 발견")

    for i in range(cnt):
        container = containers.nth(i)
        p_tags = container.locator("p")
        if p_tags.count() < 3:
            continue

        title = normalize_text(p_tags.nth(0).inner_text())
        date_text = normalize_text(p_tags.nth(1).inner_text())
        section = normalize_text(p_tags.nth(2).inner_text())

        # UPCOMING 쪽에 의미 없는 블록이 섞이면 걸러내기(기존 로직 유지)
        if mode == "upcoming":
            raw = normalize_text(container.inner_text())
            if "202" not in raw:
                continue

        href = (links.nth(i).get_attribute("href") or "") if links.count() > i else ""
        detail_url = urljoin(SETTINGS.base_url, href) if href else ""

        src = (thumbs.nth(i).get_attribute("src") or "") if thumbs.count() > i else ""
        thumb_url = urljoin(SETTINGS.base_url, src) if src else ""

        start_date, end_date = parse_operating_day(date_text)

        ex: Exhibition = {
            "title": title,
            "address": section,
            "start_date": start_date,
            "end_date": end_date,
            "open_time": SETTINGS.open_time,
            "close_time": SETTINGS.close_time,
            "gallery_name": SETTINGS.gallery_name,
            "author": "",
            "description": "",
            "img_url": [thumb_url] if thumb_url else [],
        }

        items.append({"ex": ex, "detail_url": detail_url})

    return items


# ==============================
# 상세 수집
# ==============================

def _extract_description_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
        element.decompose()

    all_text = soup.get_text(separator=" |LINE| ")
    lines = all_text.split(" |LINE| ")

    garbage_keywords = [
        "통인화랑", "tong-in",
        "게시물", "댓글", "답글",
        "공지", "알려줍니다",
        "로그인", "login",
        "all right reserved", "copyright", "insadong",
    ]

    valid: List[str] = []
    for line in lines:
        clean = line.strip()
        if not clean:
            continue

        low = clean.lower()
        if any(kw in low for kw in garbage_keywords):
            continue

        # 너무 짧은 문장 제거
        if len(clean) < 15:
            continue

        # 한글 포함만 채택
        if re.search(r"[가-힣]", clean):
            valid.append(clean)

    return "\n".join(valid).strip()


def _extract_gallery_images(page: Page) -> List[str]:
    """
    사이트의 갤러리 이미지가 div에 data-src/data-bg로 들어가는 구조 대응
    """
    raw = page.evaluate(
        """() => {
            const imgs = [];
            const targets = document.querySelectorAll(
                'div._gallery_wrap ._img_wrap, div.img_wrap._img_wrap'
            );
            targets.forEach(el => {
                let src = el.getAttribute('data-src') || el.getAttribute('data-bg');
                if (src) {
                    src = src.replace(/^url\\(['"]?/, '').replace(/['"]?\\)$/, '');
                    imgs.push(src);
                }
            });
            return imgs;
        }"""
    )

    urls: List[str] = []
    for src in raw or []:
        if not src:
            continue
        s = src.strip()
        if not s:
            continue
        if s.startswith("http"):
            urls.append(s)
        else:
            urls.append(urljoin(SETTINGS.base_url, s))

    return uniq_keep_order(urls)


def _enrich_detail(page: Page, item: _ListItem) -> None:
    ex = item["ex"]
    url = item["detail_url"]
    if not url:
        return

    print(f"👉 이동: {ex['title']} -> {url}")

    try:
        page.goto(url, timeout=SETTINGS.timeout_ms)
        page.wait_for_load_state("networkidle")
        _page_end_and_wait(page)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        print(f"   ❌ 로딩 에러: {e}")
        return

    # 텍스트(설명)
    html = page.content()
    desc = _extract_description_from_html(html)
    ex["description"] = desc

    if desc:
        print(f"   ✅ 설명: {desc[:30].replace(chr(10), ' ')}...")
    else:
        print("   ⚠️ 설명을 찾지 못했습니다.")

    # 이미지
    detail_imgs = _extract_gallery_images(page)
    ex["img_url"] = uniq_keep_order((ex.get("img_url") or []) + detail_imgs)


# ==============================
# 공개 API
# ==============================

def crawl() -> List[Exhibition]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=SETTINGS.user_agent)
        page = context.new_page()

        print(f"[접속] {SETTINGS.base_url}")
        try:
            page.goto(SETTINGS.base_url, timeout=SETTINGS.timeout_ms)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(SETTINGS.wait_ms)
        except Exception as e:
            print(f"[에러] 접속 실패: {e}")
            browser.close()
            return []

        items: List[_ListItem] = []
        items += _collect_section(page, "ON VIEW", mode="onview")
        items += _collect_section(page, "UPCOMING", mode="upcoming")

        print(f"\n[리스트 완료] 총 {len(items)}개 수집됨.\n")

        for it in items:
            _enrich_detail(page, it)

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

        out_path = json_dir / "tongInGallery.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
