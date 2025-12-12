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

LIST_URL = "https://galleryeun.com/index.php?module=Board&action=SiteBoard&sMode=SELECT_FORM&iBrdNo=1"
GALLERY_NAME = "갤러리은"

DEFAULT_OPERATING_HOUR_RAW = "AM 10:30 ~ PM 18:30(연중무휴)"
DEFAULT_ADDRESS_FALLBACK = "서울 종로구 인사동길 45-1"


# ==============================
# 날짜/시간 파싱 유틸
# ==============================

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.12.3', '2025. 12. 03', '12.8', '8'
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()
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
    '2025. 11. 26 - 2025. 12. 15'
    '2025.12.3-12.8'
    '2025.12.3 ~ 12.8'
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


def normalize_text(s: str) -> str:
    if not s:
        return ""
    return s.strip()


# ==============================
# 크롤러 본체
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

        # -------------------------------------------------------------
        # [1] 리스트 페이지 크롤링
        # -------------------------------------------------------------
        print(">>> [1단계] 리스트 페이지 접속 중...")
        try:
            page.goto(LIST_URL, timeout=60_000)
            page.wait_for_load_state("domcontentloaded")
            try:
                page.wait_for_selector(".slick-list .slick-slide", timeout=5000)
            except Exception:
                print("⚠️ 슬라이더를 찾을 수 없습니다. URL 또는 셀렉터를 확인해주세요.")
                browser.close()
                return []
        except Exception as e:
            print(f"[오류] 페이지 접속 실패: {e}")
            browser.close()
            return []

        exhibitions: List[Dict[str, Any]] = []

        slides = page.locator(".slick-list .slick-slide:not(.slick-cloned)")
        count = slides.count()
        print(f"[리스트] 발견된 원본 전시 개수: {count}")

        for i in range(count):
            slide = slides.nth(i)

            # 링크
            link_el = slide.locator("a").first
            if not link_el.count():
                continue

            href = link_el.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # 제목
            title_el = slide.locator("p.st1 span").first
            raw_title = title_el.inner_text() if title_el.count() else ""
            title_kr = " ".join(raw_title.split())

            # subtitle / 기간
            st2_texts = slide.locator("p.st2").all_inner_texts()
            st2_texts = [t.strip() for t in st2_texts if t.strip()]

            subtitle = ""
            operating_day = ""
            if len(st2_texts) >= 2:
                subtitle = st2_texts[0]
                operating_day = st2_texts[-1]
            elif len(st2_texts) == 1:
                if any(ch.isdigit() for ch in st2_texts[0]):
                    operating_day = st2_texts[0]
                else:
                    subtitle = st2_texts[0]

            # 썸네일 (style url())
            thumb_url = ""
            img_dummy = slide.locator(".img_dummy").first
            if img_dummy.count():
                style_attr = img_dummy.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style_attr)
                if m:
                    thumb_url = urljoin(LIST_URL, m.group(1).strip())

            # 날짜/시간 파싱
            start_date, end_date = parse_operating_day(operating_day)
            open_time, close_time = parse_operating_hour(DEFAULT_OPERATING_HOUR_RAW)

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": open_time,
                    "close_time": close_time,
                    "gallery_name": GALLERY_NAME,

                    # 주소/상세에서 채움
                    "address": "",
                    "author": "",          # DB 스키마에 맞춰 author로 통일
                    "description": "",
                    "img_url": [thumb_url] if thumb_url else [],

                    # 내부 처리용(최종 출력 전 제거)
                    "_detail_url": detail_url,
                    "_subtitle": subtitle,
                }
            )

        print(f"[리스트] 총 {len(exhibitions)}개 리스트 확보 완료.\n")

        # -------------------------------------------------------------
        # [2] 상세 페이지 크롤링
        # -------------------------------------------------------------
        for idx, ex in enumerate(exhibitions):
            url = ex.get("_detail_url", "")
            if not url:
                continue

            print(f"[{idx + 1}/{len(exhibitions)}] 상세 이동: {ex['title']}")

            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("domcontentloaded")
                try:
                    page.wait_for_selector(".t_st2", timeout=3000)
                except Exception:
                    pass
            except Exception as e:
                print(f"  -> [오류] 상세 페이지 접속 실패: {e}")
                continue

            # (1) 이미지 수집 (style 속성 url())
            images = ex.get("img_url", [])[:]

            hero_imgs = page.locator(".ex_li .img_dummy").all()
            for el in hero_imgs:
                style = el.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style)
                if m:
                    images.append(urljoin(url, m.group(1).strip()))

            gallery_imgs = page.locator(".container .gal_dummy").all()
            for el in gallery_imgs:
                style = el.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style)
                if m:
                    images.append(urljoin(url, m.group(1).strip()))

            ex["img_url"] = list(dict.fromkeys(images))
            print(f"  -> 이미지: {len(ex['img_url'])}개 수집")

            # (2) 작가 후보 수집
            artist_list: List[str] = []

            def add_artist_name(name: str):
                name = name.strip()
                if name and name not in artist_list:
                    artist_list.append(name)

            gallery_blocks = page.locator("a.gallery")
            for i_g in range(gallery_blocks.count()):
                a_g = gallery_blocks.nth(i_g)
                title_attr = (a_g.get_attribute("title") or "").strip()
                if title_attr:
                    add_artist_name(title_attr.split(",")[0].strip())

                p_el = a_g.locator("xpath=./following-sibling::a[1]/p")
                if p_el.count():
                    p_text = p_el.inner_text().strip()
                    add_artist_name(p_text.split(",")[0].strip())

            # (3) 본문 텍스트(.t_st2): description/profile/참여 작가 분리
            description = ""
            profile = ""
            artist_section_text = ""

            text_container = page.locator(".t_st2").first
            if text_container.count():
                full_text = text_container.inner_text()

                split_keyword = None
                if "[ Profile ]" in full_text:
                    split_keyword = "[ Profile ]"
                elif "[프로필]" in full_text:
                    split_keyword = "[프로필]"

                if split_keyword:
                    before_profile, after_profile = full_text.split(split_keyword, 1)
                    description_base = before_profile.strip()
                    profile = (split_keyword + "\n" + after_profile.strip()).strip()
                else:
                    description_base = full_text.strip()

                if "참여 작가" in description_base:
                    before_artist, after_artist = description_base.split("참여 작가", 1)
                    description = before_artist.strip()
                    artist_section_text = after_artist
                else:
                    description = description_base

                if "참여 작가" in full_text and not artist_section_text:
                    _, artist_section_text = full_text.split("참여 작가", 1)

                if artist_section_text:
                    lines_after = [ln.strip() for ln in artist_section_text.splitlines() if ln.strip()]
                    if lines_after:
                        joined = " ".join(lines_after)
                        for cand in re.split(r"[、,]", joined):
                            add_artist_name(cand)

                # fallback: 첫 줄에서 "개인전/초대전" 제거
                lines = [line.strip() for line in full_text.splitlines() if line.strip()]
                if lines:
                    first_line = lines[0]
                    fallback_name = first_line.replace("개인전", "").replace("초대전", "").strip()
                    add_artist_name(fallback_name)

            ex["description"] = normalize_text(description)
            ex["author"] = ", ".join(artist_list).strip()

            # 주소 추출
            footer = page.locator("address").first
            if footer.count():
                ex["address"] = footer.inner_text().strip()
            else:
                ex["address"] = DEFAULT_ADDRESS_FALLBACK

            # profile은 필요하면 별도 필드로 남길 수 있는데,
            # DB 스키마에는 없으니 _meta로 보관(원하면 삭제)
            ex["_artist_profile"] = profile

        browser.close()

        # 내부 키 제거
        for ex in exhibitions:
            ex.pop("_detail_url", None)
            ex.pop("_subtitle", None)
            # 원하면 profile도 제거 가능
            # ex.pop("_artist_profile", None)

        return exhibitions


def run(save_json: bool = True) -> List[Dict[str, Any]]:
    """
    ✅ runner.py가 이 함수를 호출하도록 맞추는 '엔트리 함수'
    """
    data = crawl()

    if save_json:
        json_dir = Path(__file__).resolve().parent / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        out_path = json_dir / "galleryEun.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[JSON] 저장 완료: {out_path}")

    return data


if __name__ == "__main__":
    run(save_json=True)
