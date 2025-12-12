from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class Settings:
    target_url: str = "https://seoulnoin.or.kr/senior/space2.asp"
    gallery_name: str = "서울노인복지센터 탑골미술관"
    gallery_address: str = "서울시 종로구 삼일대로 467 서울노인복지센터 1층"
    open_time: str = "10:00"
    close_time: str = "18:00"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )


SETTINGS = Settings()


def normalize_text(s: str) -> str:
    return s.strip() if s else ""


def parse_date_range(text: str) -> Tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None

    cleaned = text.replace("&nbsp;", " ").strip()

    pattern = (
        r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s*[-~]\s*"
        r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})"
    )
    m = re.search(pattern, cleaned)
    if not m:
        return None, None

    y1, m1, d1, y2, m2, d2 = map(int, m.groups())
    try:
        start = datetime(y1, m1, d1).strftime("%Y-%m-%d")
        end = datetime(y2, m2, d2).strftime("%Y-%m-%d")
        return start, end
    except ValueError:
        return None, None


def crawl() -> List[Dict[str, Any]]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=SETTINGS.user_agent)
        page = context.new_page()

        try:
            page.goto(SETTINGS.target_url, timeout=60_000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"[에러] 페이지 접속 실패: {e}")
            browser.close()
            return []

        title = ""
        title_el = page.locator("p.fs30.bold.black")
        if title_el.count():
            title = normalize_text(title_el.first.inner_text())

        start_date, end_date = None, None
        date_el = page.locator(".smInfo1 li.point")
        if date_el.count():
            date_text = normalize_text(date_el.first.inner_text())
            start_date, end_date = parse_date_range(date_text)

        description = ""
        summary_title = page.locator("div.first_title", has_text="전시요약")
        if summary_title.count():
            target_div = summary_title.locator("xpath=following-sibling::div[2]")
            if target_div.count():
                raw = target_div.first.inner_text()
                lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
                description = "\n\n".join(lines).strip()
            else:
                fallback_div = summary_title.locator("xpath=following-sibling::div[1]")
                if fallback_div.count():
                    description = normalize_text(fallback_div.first.inner_text())

        img_urls: List[str] = []
        target_img = page.locator("img[alt='전시이미지']")
        if not target_img.count():
            target_img = page.locator("img[src*='upload']")

        for i in range(target_img.count()):
            src = target_img.nth(i).get_attribute("src")
            if src:
                img_urls.append(urljoin(SETTINGS.target_url, src.strip()))

        img_urls = list(dict.fromkeys(img_urls))
        browser.close()

        if not title or not end_date:
            return []

        return [{
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "description": description,
            "img_url": img_urls,
            "address": SETTINGS.gallery_address,
            "gallery_name": SETTINGS.gallery_name,
            "open_time": SETTINGS.open_time,
            "close_time": SETTINGS.close_time,
            "author": "",
        }]


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "seoulNoin.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
