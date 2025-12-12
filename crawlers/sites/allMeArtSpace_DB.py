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

# 올미아트스페이스 현재 전시 URL
LIST_URL = "http://www.allmeartspace.com/b/exhibitions/?state=current"

GALLERY_NAME = "올미아트스페이스"
GALLERY_ADDRESS_DEFAULT = "서울 종로구 우정국로 51 올미아트스페이스"
DEFAULT_OPEN_TIME_STR = "10:30"
DEFAULT_CLOSE_TIME_STR = "18:00"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 26', '2025.12.3', '12.8', '8', '2025-08-25' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()

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
    예시:
      '2025-11-27(목) ~2025-12-30(화)' -> ('2025-11-27', '2025-12-30')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 0) 문자열 안에서 YYYY-MM-DD 패턴 2개 뽑기 (요일 괄호 포함 대응)
    found = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(found) >= 2:
        try:
            dt1 = datetime.strptime(found[0], "%Y-%m-%d")
            dt2 = datetime.strptime(found[1], "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 1) YYYY-MM-DD - YYYY-MM-DD 형식
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", text)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 2) 그 외: ~, -, – 기준으로 나누기
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


def normalize_text(s: str) -> str:
    """None/공백/줄바꿈 정리해서 의미 있는 텍스트만 남김"""
    if not s:
        return ""
    return s.strip()


# ==============================
# 크롤러 본체 (올미아트스페이스)
# ==============================

def crawl() -> List[Dict[str, Any]]:
    """
    ✅ 회사 스타일: 크롤링만 담당하고 list[dict] 반환
    DB 저장은 runner/db.py에서 공통 처리
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 현재 전시 리스트 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions: List[Dict[str, Any]] = []
        detail_urls: List[str] = []

        items = page.locator("div.cbp-item")
        count = items.count()
        print(f"[리스트] 전시 개수(올미): {count}")

        if count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. div.cbp-item 구조를 다시 확인해 주세요.")
            browser.close()
            return []

        for i in range(count):
            item = items.nth(i)

            # (1) 제목
            title_el = item.locator("a.cbp-l-grid-masonry-projects-title")
            raw_title = title_el.first.inner_text() if title_el.count() else item.inner_text()
            title_kr = " ".join(raw_title.split())

            # (2) 상세 페이지 URL
            link_el = item.locator("a.cbp-caption-activeWrap")
            if not link_el.count():
                link_el = item.locator("a.cbp-l-grid-masonry-projects-title")
            if not link_el.count():
                print(f"[리스트] 링크 없음, 스킵: {title_kr}")
                continue

            href = link_el.first.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (3) 기간 텍스트
            operating_day_el = item.locator("div.cbp-l-grid-masonry-projects-desc")
            operating_day = operating_day_el.first.inner_text().strip() if operating_day_el.count() else ""
            start_date, end_date = parse_operating_day(operating_day)

            # (4) 리스트 썸네일
            thumb_urls: List[str] = []
            thumb_img = item.locator(".cbp-caption-defaultWrap img")
            if thumb_img.count():
                src = thumb_img.first.get_attribute("src")
                if src:
                    src = src.strip()
                    if src and "/data/file" in src:
                        thumb_urls.append(urljoin(LIST_URL, src))

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": GALLERY_ADDRESS_DEFAULT,
                    "gallery_name": GALLERY_NAME,
                    "open_time": DEFAULT_OPEN_TIME_STR,
                    "close_time": DEFAULT_CLOSE_TIME_STR,
                    "author": "",       # 상세에서 채움
                    "description": "",  # 상세에서 채움
                    "img_url": thumb_urls,
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) 상세 크롤링
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (0) 장소(있으면)
            hall_detail = None
            info_items = page.locator("ul li")
            for k in range(info_items.count()):
                li = info_items.nth(k)
                dt_el = li.locator("dt")
                dd_el = li.locator("dd")
                if not dt_el.count() or not dd_el.count():
                    continue

                dt_text = dt_el.first.inner_text().strip()
                dd_text = dd_el.first.inner_text().strip()

                if "전시장소" in dt_text or "장소" in dt_text:
                    hall_detail = dd_text
                    break
            if hall_detail:
                ex["address"] = hall_detail

            # (1) 작가 정보 (있으면)
            artist = ""
            rows = page.locator("tr")
            for j in range(rows.count()):
                row = rows.nth(j)
                cells = row.locator("th, td")
                if cells.count() < 2:
                    continue
                label = cells.nth(0).inner_text().strip()
                if "작가" in label or "Artist" in label:
                    artist = "".join(cells.nth(1).inner_text().split())
                    break

            # (2) 설명
            content = None
            content_selectors = [
                "div.exhibition_view",
                "div.view",
                "div.view_cont",
                "div.board_view",
                "div#content",
                "article",
            ]
            for sel in content_selectors:
                c = page.locator(sel)
                if c.count() and c.inner_text().strip():
                    content = c
                    break

            if content is None:
                paragraphs = page.locator("p").all_inner_texts()
            else:
                p_loc = content.locator("p")
                paragraphs = p_loc.all_inner_texts() if p_loc.count() else [content.inner_text()]

            lines = [p.strip() for p in paragraphs if p.strip()]
            description = "\n".join(lines).strip()

            # (3) 이미지: '/data/file' 만
            image_urls = list(ex.get("img_url", []))
            img_els = page.locator("img")
            for idx in range(img_els.count()):
                src = img_els.nth(idx).get_attribute("src")
                if not src:
                    continue
                src = src.strip()
                if not src or "/data/file" not in src:
                    continue
                image_urls.append(urljoin(url, src))

            ex["author"] = artist
            ex["description"] = description
            ex["img_url"] = list(dict.fromkeys(image_urls))  # 중복 제거

            print(f"[상세] 이미지 개수: {len(ex['img_url'])}, 설명 길이: {len(normalize_text(description))}")

        browser.close()
        print(f"\n[최종] 올미 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "allMeArtSapce.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    # 단독 실행도 가능하게만 유지
    run(save_json=True)
