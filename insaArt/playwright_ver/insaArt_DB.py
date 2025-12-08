from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime, date
import os

import psycopg2  # PostgreSQL 연동용


# 인사아트센터 현재 전시 URL
LIST_URL = "https://www.insaartcenter.com/bbs/board.php?bo_table=exhibition_current"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 26', '2025.12.3', '12.8', '8' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜 (datetime 또는 None)
    """
    if not part:
        return None

    s = part.strip()
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
      '2025. 11. 26 - 2025. 12. 15'  -> ('2025-11-26', '2025-12-15')
      '2025.12.3-12.8'               -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 12.8'             -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 2025.12.8'        -> ('2025-12-03', '2025-12-08')
    실패 시: (원본 문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # ~, -, – 기준으로 앞/뒤 나누기
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
    예시: 'AM 10:00 ~ PM 19:00(화요일 정기 휴무)'
    -> ('AM 10:00', 'PM 19:00')
    (컬럼이 TEXT/VARCHAR 라고 가정하고 문자열 그대로 넣음)
    """
    if not operating_hour:
        return "", ""

    base = operating_hour.split("(", 1)[0].strip()
    parts = [p.strip() for p in base.split("~")]
    if len(parts) != 2:
        return base, ""

    open_time = parts[0]
    close_time = parts[1]
    return open_time, close_time


def to_date_or_none(s: str):
    """'YYYY-MM-DD' 를 date 객체로 변환, 아니면 None"""
    if not s:
        return None
    s = s.strip()
    if len(s) != 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


# ==============================
# 크롤러 본체
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 현재 전시 리스트 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        detail_urls = []

        # ▶ 각 전시 블록: div.gall_text_href
        items = page.locator("div.gall_text_href")
        count = items.count()
        print(f"[리스트] 전시 개수(div.gall_text_href): {count}")

        for i in range(count):
            item = items.nth(i)

            # (1) 제목 + 상세 페이지 URL
            link = item.locator("a.bo_tit")
            if not link.count():
                continue

            raw_title = link.inner_text()
            title_kr = " ".join(raw_title.split())

            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (2) 기간 / 전시장 / 갤러리명
            rows = item.locator(".list-spec table tr")
            row_count = rows.count()

            operating_day = rows.nth(0).inner_text().strip() if row_count > 0 else ""
            hall = rows.nth(1).inner_text().strip() if row_count > 1 else ""
            gallery_txt = rows.nth(2).inner_text().strip() if row_count > 2 else ""

            # operating_day → start_date / end_date
            start_date, end_date = parse_operating_day(operating_day)

            # 운영시간 고정값 → open_time / close_time
            operating_hour = "AM 10:00 ~ PM 19:00(화요일 정기 휴무)"
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": hall,
                    "galleryName": gallery_txt or "인사아트센터",
                    "open_time": open_time,
                    "close_time": close_time,
                    "artist": "",
                    "description": "",
                    "img_url": [],   # ← snake_case로 사용
                }
            )

            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) 각 전시별 상세 페이지 크롤링
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 작가 정보
            artist = ""
            spec = page.locator("div.spec")
            if spec.count():
                rows = spec.locator("table tr")
                row_count = rows.count()

                for j in range(row_count):
                    th = rows.nth(j).locator("th")
                    if not th.count():
                        continue

                    label = th.inner_text().strip()
                    if label == "작가":
                        td = rows.nth(j).locator("td")
                        if td.count():
                            artist = "".join(td.inner_text().split())
                        break

            # (2) 설명 텍스트
            content = page.locator("#bo_v_con")
            if not content.count():
                content = page.locator(".bo_v_con")

            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = p_loc.all_inner_texts()
                else:
                    paragraphs = [content.inner_text()]
            else:
                paragraphs = page.locator("p").all_inner_texts()

            description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # (3) 이미지 URL
            image_urls = []

            gallery_items = page.locator("#img-gallery li")
            item_count = gallery_items.count()

            for idx in range(item_count):
                li = gallery_items.nth(idx)

                src = li.get_attribute("data-src")
                if not src:
                    img_el = li.locator("img")
                    if img_el.count():
                        src = img_el.nth(0).get_attribute("src")

                if not src:
                    continue

                src = src.strip()
                if not src:
                    continue

                if "/data/file/exhibition_current/" not in src:
                    continue

                image_urls.append(src)

            image_urls = list(dict.fromkeys(image_urls))

            ex["artist"] = artist
            ex["description"] = description
            ex["img_url"] = image_urls   # ← 여기서도 image_url 로 통일

            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 예시 스키마 (가정):

      title        VARCHAR(...) NOT NULL
      description  VARCHAR(...) NOT NULL
      address      VARCHAR(...)
      author       VARCHAR(...) NOT NULL
      start_date   DATE
      end_date     DATE NOT NULL
      open_time    TEXT (또는 TIME)
      close_time   TEXT (또는 TIME)
      views        INTEGER NOT NULL
      img_url    VARCHAR(255)[] NOT NULL   -- 배열 타입이라고 가정
      galleryName  VARCHAR(...)
      phoneNum     VARCHAR(...)
      createdAt    DATE NOT NULL
      modifiedAt   DATE
    """
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")  # 필요하면 localhost 등으로 변경
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
         "galleryName", "phoneNum",
         "createdAt", "modifiedAt")
        VALUES (%s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s)
        """

        today = date.today()

        for ex in exhibitions:
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            # end_date NOT NULL이면 없는 건 스킵
            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                continue

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    ex.get("description") or "",
                    ex.get("address"),
                    ex.get("artist") or "",
                    start_dt,
                    end_dt,
                    ex.get("open_time") or None,
                    ex.get("close_time") or None,
                    0,                              # views 기본 0
                    ex.get("img_url", []),        # image_url: 리스트 그대로
                    ex.get("galleryName"),
                    None,                           # phoneNum
                    today,
                    None,
                ),
            )

        conn.commit()
        print(f"[DB] exhibition 테이블에 {len(exhibitions)}개 INSERT 시도 완료")

    except Exception as e:
        print("[DB] 에러 발생:", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ==============================
# 메인 실행부
# ==============================

if __name__ == "__main__":
    data = crawl_exhibitions()

    # 1) JSON 파일로 저장
    output_path = "insaArt.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

    # 2) DB에 저장
    save_to_postgres(data)
