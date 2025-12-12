from __future__ import annotations

import re
import json
from urllib.parse import urljoin
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from playwright.sync_api import sync_playwright


# ==============================
# 기본 설정
# ==============================

LIST_URL = "http://www.gallerymeme.com/web/current.html"

GALLERY_NAME = "갤러리밈"
BASE_ADDRESS = "갤러리밈"  # 필요하면 실제 주소로 교체

# 사이트에 운영시간 정보가 없어서 고정값 사용
DEFAULT_OPEN_TIME_STR = "10:30"
DEFAULT_CLOSE_TIME_STR = "18:30"


# ==============================
# 유틸
# ==============================

def normalize_text(s: str) -> str:
    if not s:
        return ""
    return s.strip()


def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.11.12.', '2025. 11. 26', '2025.12.3', '12.8', '8', '2025-08-25'
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip().rstrip(".")

    # 0) YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # "2025. 11. 26" -> "2025.11.26"
    s = re.sub(r"\s*\.\s*", ".", s)

    # 1) YYYY.MM.DD
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    if base_date:
        # 2) MM.DD
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

        # 3) DD
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
    예시:
      '2025.11.12. ~ 2025.12.21.'
      '2025-01-05 ~ 2025-02-03'
      '2025. 1. 5 - 2025. 2. 3'
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 0) 문자열에서 YYYY-MM-DD 2개 있으면 그걸 우선
    found = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if len(found) >= 2:
        dt1 = parse_single_date(found[0])
        dt2 = parse_single_date(found[1])
        if dt1 and dt2:
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")

    # 1) ~, -, – 기준 split
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


# ==============================
# 크롤러 본체 (갤러리밈)
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일:
    - 크롤링만 수행하고 list[dict] 반환
    - DB 저장은 runner/common db 모듈에서 처리
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []

        items = page.locator("a[href*='exbView']")
        raw_count = items.count()
        print(f"[리스트] 전시 a태그 개수(갤러리밈): {raw_count}")

        if raw_count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. a[href*='exbView'] 셀렉터를 확인해 주세요.")
            browser.close()
            return []

        seen_detail_urls = set()
        detail_urls: List[str] = []

        for i in range(raw_count):
            item = items.nth(i)

            href = (item.get_attribute("href") or "").strip()
            if not href:
                continue

            detail_url = urljoin(LIST_URL, href)
            if detail_url in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_url)

            # 제목
            title_el = item.locator(".cur_title")
            raw_title = title_el.inner_text() if title_el.count() else item.inner_text()
            title_kr = " ".join(raw_title.split())

            # 작가
            artist_el = item.locator(".cur_artist")
            author = artist_el.inner_text().strip() if artist_el.count() else ""

            # 전시장 정보(카테고리) -> address
            cate_el = item.locator(".cur_cate")
            cate_text = cate_el.inner_text().strip() if cate_el.count() else ""
            address = cate_text or BASE_ADDRESS

            # 전시 기간
            date_el = item.locator(".cur_date")
            operating_day = date_el.inner_text().strip() if date_el.count() else ""
            start_date, end_date = parse_operating_day(operating_day)

            # 썸네일
            thumb_urls: List[str] = []
            img_el = item.locator("figure img")
            if img_el.count():
                src = (img_el.first.get_attribute("src") or "").strip()
                if src:
                    thumb_urls.append(urljoin(LIST_URL, src))

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": DEFAULT_OPEN_TIME_STR,
                    "close_time": DEFAULT_CLOSE_TIME_STR,
                    "address": address,
                    "gallery_name": GALLERY_NAME,
                    "author": author,
                    "description": "",
                    "img_url": thumb_urls,

                    # 내부용
                    "_detail_url": detail_url,
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 중복 제거 후 수집된 전시 수: {len(exhibitions)}")

        # -----------------------------
        # 상세 페이지 크롤링
        # -----------------------------
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")

            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 작가 정보(상세에서 있으면 갱신)
            author = ex.get("author", "")
            rows = page.locator("tr")
            for j in range(rows.count()):
                row = rows.nth(j)
                cells = row.locator("th, td")
                if cells.count() < 2:
                    continue

                label = cells.nth(0).inner_text().strip()
                if "작가" in label or "Artist" in label:
                    author = "".join(cells.nth(1).inner_text().split())
                    break

            # (2) 설명: p[class='0'] 만 사용 + 이력/경력 시작되면 컷
            paras0 = page.locator("p[class='0']")
            raw_paras = paras0.all_inner_texts() if paras0.count() else []
            raw_lines = [p.strip() for p in raw_paras if p.strip()]

            filtered_lines: List[str] = []
            for line in raw_lines:
                l = line.strip()
                lower = l.lower()

                # 이력 시작 신호 -> 여기서부터 컷
                if re.match(r"^b\.\s*\d{4}", l):
                    break
                if any(kw in lower for kw in [
                    "solo exhibition",
                    "solo exhibitions",
                    "group exhibition",
                    "group exhibitions",
                    "collections",
                    "awards",
                ]):
                    break

                filtered_lines.append(l)

            description = "\n".join(filtered_lines).strip()

            # (3) 이미지: upload/data/file 경로만
            image_urls = list(ex.get("img_url", []))

            img_els = page.locator("img")
            for idx in range(img_els.count()):
                src = (img_els.nth(idx).get_attribute("src") or "").strip()
                if not src:
                    continue
                if "/upload/" not in src and "/data/file" not in src:
                    continue
                image_urls.append(urljoin(url, src))

            ex["author"] = author
            ex["description"] = description
            ex["img_url"] = list(dict.fromkeys(image_urls))

            print(f"[상세] 이미지: {len(ex['img_url'])}개 / 설명 길이: {len(normalize_text(description))}")

        browser.close()

        # 내부 키 제거
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

        out_path = json_dir / "galleryMeme.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
