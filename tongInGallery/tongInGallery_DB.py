from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime, date, time as dt_time
import time
import os

import psycopg2

BASE_URL = "http://tongingallery.com/exhibitions"


# 유틸 함수

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
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


def parse_operating_day(operating_day: str):
    if not operating_day:
        return "", ""
    text = operating_day.strip()
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        return operating_day, ""

    start_part, end_part = parts[0], parts[1]

    start_dt = parse_single_date(start_part)
    if not start_dt:
        return operating_day, ""

    end_dt = parse_single_date(end_part, base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def to_date_or_none(s: str):
    """'YYYY-MM-DD' -> date 객체, 실패 시 None"""
    if not s:
        return None
    s = s.strip()
    if len(s) != 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def to_time_or_none(s: str):
    """'HH:MM' -> time 객체, 실패 시 None"""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return None


# 크롤러 본체

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        print(f"접속 중: {BASE_URL}")
        try:
            page.goto(BASE_URL, timeout=60_000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"접속 실패: {e}")
            return []

        exhibitions = []

        # 1. ON VIEW 목록 수집
        onview_header = page.locator("h6", has_text="ON VIEW").first
        if onview_header.count():
            header_row = onview_header.locator(
                "xpath=ancestor::div[contains(@class,'doz_row')][1]"
            )
            image_row = header_row.locator(
                "xpath=following-sibling::div[contains(@class,'doz_row')][2]"
            )
            text_row = header_row.locator(
                "xpath=following-sibling::div[contains(@class,'doz_row')][3]"
            )

            cnt = text_row.locator("div.text-table").count()
            links = image_row.locator("a._fade_link")
            thumbs = image_row.locator("img.org_image")

            print(f"[ON VIEW] {cnt}개 발견")

            for i in range(cnt):
                container = text_row.locator("div.text-table").nth(i)
                p_tags = container.locator("p")
                if p_tags.count() < 3:
                    continue

                title = p_tags.nth(0).inner_text().strip()
                date_text = p_tags.nth(1).inner_text().strip()
                section = p_tags.nth(2).inner_text().strip()

                href = links.nth(i).get_attribute("href") if links.count() > i else ""
                detail_url = urljoin(BASE_URL, href) if href else ""

                src = thumbs.nth(i).get_attribute("src") if thumbs.count() > i else ""
                thumb_url = urljoin(BASE_URL, src) if src else ""

                start_date, end_date = parse_operating_day(date_text)

                # 통인화랑 운영시간
                open_time = "10:30"
                close_time = "18:30"

                exhibitions.append(
                    {
                        "title": title,
                        "address": section,
                        "start_date": start_date,
                        "end_date": end_date,
                        "open_time": open_time,
                        "close_time": close_time,
                        "gallery_name": "통인화랑",
                        "detail_url": detail_url,
                        "author": "",
                        "description": "",
                        "img_url": [thumb_url] if thumb_url else [],
                    }
                )

        # 1-2. UPCOMING 목록 수집
        upcoming_header = page.locator("h6", has_text="UPCOMING").first
        if upcoming_header.count():
            inside = upcoming_header.locator(
                "xpath=ancestor::div[contains(@class,'inside')][1]"
            )
            up_cnt = inside.locator("div.text-table").count()
            up_links = inside.locator("a._fade_link")
            up_thumbs = inside.locator("img.org_image")

            print(f"[UPCOMING] {up_cnt}개 발견")

            for i in range(up_cnt):
                div = inside.locator("div.text-table").nth(i)
                raw = div.inner_text()
                if "202" not in raw:
                    continue

                p_tags = div.locator("p")
                if p_tags.count() < 3:
                    continue

                title = p_tags.nth(0).inner_text().strip()
                date_text = p_tags.nth(1).inner_text().strip()
                section = p_tags.nth(2).inner_text().strip()

                href = up_links.nth(i).get_attribute("href") if up_links.count() > i else ""
                detail_url = urljoin(BASE_URL, href) if href else ""

                src = up_thumbs.nth(i).get_attribute("src") if up_thumbs.count() > i else ""
                thumb_url = urljoin(BASE_URL, src) if src else ""

                start_date, end_date = parse_operating_day(date_text)

                open_time = "10:30"
                close_time = "18:30"

                exhibitions.append(
                    {
                        "title": title,
                        "address": section,
                        "start_date": start_date,
                        "end_date": end_date,
                        "open_time": open_time,
                        "close_time": close_time,
                        "gallery_name": "통인화랑",
                        "detail_url": detail_url,
                        "author": "",
                        "description": "",
                        "img_url": [thumb_url] if thumb_url else [],
                    }
                )

        print(f"\n[리스트 완료] 총 {len(exhibitions)}개 수집됨.\n")

        # 2. 상세 페이지 순회
        for ex in exhibitions:
            url = ex.get("detail_url")
            if not url:
                continue

            print(f"이동: {ex['title']} -> {url}")
            try:
                page.goto(url, timeout=60_000)
                page.keyboard.press("End")
                page.wait_for_load_state("networkidle")
                time.sleep(1)
            except Exception as e:
                print(f"   로딩 에러: {e}")
                continue

            ex["author"] = ""

            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
                element.decompose()

            all_text = soup.get_text(separator=" |LINE| ")
            lines = all_text.split(" |LINE| ")

            valid_lines = []

            garbage_keywords = [
                "통인화랑", "tong-in",
                "게시물", "댓글", "답글",
                "공지", "알려줍니다",
                "로그인", "login",
                "all right reserved", "copyright", "insadong"
            ]

            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue

                lower_line = clean_line.lower()

                if any(kw in lower_line for kw in garbage_keywords):
                    continue

                if len(clean_line) < 15:
                    continue

                if re.search(r"[가-힣]", clean_line):
                    valid_lines.append(clean_line)

            description = "\n".join(valid_lines).strip()
            ex["description"] = description

            if description:
                print(f"   최종 설명: {description[:30].replace('\n', ' ')}...")
            else:
                print("   설명을 찾지 못했습니다.")

            gallery_images = page.evaluate(
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

            current_images = ex.get("img_url", [])[:]
            for raw_src in gallery_images:
                if not raw_src:
                    continue
                full_url = raw_src.strip()
                if not full_url.startswith("http"):
                    full_url = urljoin(BASE_URL, full_url)
                current_images.append(full_url)

            ex["img_url"] = list(dict.fromkeys(current_images))

        for ex in exhibitions:
            ex.pop("detail_url", None)

        browser.close()
        return exhibitions


# DB 저장 함수

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조 (다른 크롤러와 동일 가정)
    """
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "api.insa-exhibition.shop")
    db_port = os.getenv("POSTGRES_PORT", "5432")

    conn = None
    try:
        conn = psycopg2.connect(
            dbname=db_name,
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
        )
        cur = conn.cursor()

        insert_sql = """
        INSERT INTO exhibition
        (title, description, address,
         author, start_date, end_date,
         open_time, close_time,
         views, img_url,
         gallery_name, phone_num,
         created_at, modified_at)
        VALUES (%s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s)
        """

        today = date.today()
        saved = 0
        skipped = 0

        for ex in exhibitions:
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                skipped += 1
                continue

            # description 비어있으면 저장하지 않음
            desc = (ex.get("description") or "").strip()
            if not desc:
                print(f"[DB] description 없음, 스킵: {ex.get('title')}")
                skipped += 1
                continue

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    desc,  # 정제된 description 저장
                    ex.get("address"),
                    ex.get("author") or "",
                    start_dt,
                    end_dt,
                    open_t,
                    close_t,
                    0,
                    ex.get("img_url", []),
                    ex.get("gallery_name"),
                    None,
                    today,
                    None,
                ),
            )
            saved += 1

        conn.commit()
        print(f"[DB] 저장 완료: {saved}개 / 스킵: {skipped}개")

    except Exception as e:
        print("[DB] 에러 발생:", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# 메인 실행부

if __name__ == "__main__":
    data = crawl_exhibitions()

    output_path = "tongInGallery.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

    save_to_postgres(data)
