from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime, date, time
import os

import psycopg2  # PostgreSQL 연동용


# ==============================
# 기본 설정
# ==============================

LIST_URL = "https://rhogallery.com/ko/current/"
GALLERY_NAME = "노화랑"
BASE_ADDRESS = "서울 종로구 인사동길 54 노화랑"  # 필요하면 수정

# open_time / close_time 없을 때 기본값
DEFAULT_OPEN_TIME = time(10, 0)   # 10:00
DEFAULT_CLOSE_TIME = time(18, 0)  # 18:00


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 21', '2025.11.21.', '12. 10', '8', '2025-08-25' 등
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()
    s = s.rstrip(".")  # 끝 점 제거

    # 0) YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # "2025. 11. 21" -> "2025.11.21"
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
      '2025. 11. 21 – 12. 10'
      '2025.11.21-2025.12.10'
      '2025-11-21 ~ 2025-12-10'
    실패 시: (원본 문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # YYYY-MM-DD 형식 2개 있으면 바로 사용
    found = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if len(found) >= 2:
        dt1 = parse_single_date(found[0])
        dt2 = parse_single_date(found[1])
        if dt1 and dt2:
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")

    # ~, -, – 기준으로 나누기
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
    '10:00 ~ 18:00' -> ('10:00', '18:00')
    """
    if not operating_hour:
        return "", ""

    base = operating_hour.split("(", 1)[0].strip()
    times = re.findall(r"\d{1,2}:\d{2}", base)

    if len(times) >= 2:
        return times[0], times[1]
    elif len(times) == 1:
        return times[0], ""
    else:
        return "", ""


def to_date_or_none(s: str):
    if not s:
        return None
    s = s.strip()
    if len(s) < 8:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def to_time_or_none(s: str):
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return None


# ==============================
# 크롤러 본체 (RHO GALLERY)
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

        # 리스트: 현재전시 글들은 category-00_current-exhibition 클래스를 가지고 있음
        items = page.locator("article.category-00_current-exhibition")
        count = items.count()
        print(f"[리스트] 전시 개수(rhogallery): {count}")

        if count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. 셀렉터를 다시 확인해 주세요.")
            browser.close()
            return []

        seen_detail_urls = set()

        for i in range(count):
            item = items.nth(i)

            # 상세 페이지 URL: 썸네일 롤오버 링크 우선
            link = item.locator("a.post-thumbnail-rollover")
            if link.count():
                href = link.first.get_attribute("href") or ""
            else:
                # fallback: 제목 링크
                href_el = item.locator("h3.entry-title a")
                href = href_el.first.get_attribute("href") if href_el.count() else ""

            if not href:
                continue

            href = href.strip()
            detail_url = urljoin(LIST_URL, href)

            if detail_url in seen_detail_urls:
                print(f"[리스트] 중복 URL 스킵: {detail_url}")
                continue
            seen_detail_urls.add(detail_url)

            # 제목
            title_el = item.locator("h3.entry-title")
            raw_title = title_el.inner_text() if title_el.count() else item.inner_text()
            title_kr = " ".join(raw_title.split())

            # 작가: 제목이 곧 작가명이라고 보고 author = 제목
            artist = title_kr

            # 전시 기간: entry-excerpt 안의 p
            date_el = item.locator(".entry-excerpt p")
            operating_day = date_el.inner_text().strip() if date_el.count() else ""
            start_date, end_date = parse_operating_day(operating_day)

            # 주소: 기본값 사용
            address = BASE_ADDRESS

            # 썸네일 이미지는 여기선 굳이 안 써도 되지만 초기값으로 비워 두거나 추가
            thumb_urls = []

            # 운영시간: 페이지에 없으니 빈 문자열 → DB에서 기본값으로 대체
            open_time, close_time = "", ""

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": address,
                    "gallery_name": GALLERY_NAME,
                    "open_time": "10:00",
                    "close_time": "18:00",
                    "author": artist,
                    "description": "",
                    "img_url": thumb_urls,  # 상세에서 채움
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) 상세 페이지 크롤링
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 상세에서 작가 정보가 따로 있으면 갱신 (tr 안에 '작가', 'Artist' 찾기)
            artist = ex["author"]
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

            # (2) 설명 텍스트 - wpb_wrapper 중 가장 긴 텍스트 블럭 선택 후, 그 안의 p 태그들만 사용
            wrappers = page.locator("div.wpb_text_column.wpb_content_element > div.wpb_wrapper")

            chosen = None
            max_len = 0

            w_count = wrappers.count()
            for w_idx in range(w_count):
                w = wrappers.nth(w_idx)
                txt = w.inner_text().strip()
                length = len(txt)
                if length > max_len:
                    max_len = length
                    chosen = w

            paragraphs = []
            if chosen:
                p_loc = chosen.locator("p")
                p_count = p_loc.count()
                if p_count:
                    for idx in range(p_count):
                        # p 안 span까지 포함한 전체 텍스트
                        txt = p_loc.nth(idx).inner_text().strip()
                        if txt:
                            paragraphs.append(txt)
                else:
                    txt = chosen.inner_text().strip()
                    if txt:
                        paragraphs.append(txt)

            description = "\n".join(paragraphs)

            # (3) 이미지 URL: ❗ div.vc_column-inner 안에 있는 이미지 / 링크만 크롤링
            image_urls = []

            # 우선 a 태그 href(원본 큰 이미지) 위주로 수집
            link_els = page.locator("div.vc_column-inner a[href*='wp-content/uploads']")
            for idx in range(link_els.count()):
                href = link_els.nth(idx).get_attribute("href")
                if not href:
                    continue
                href = href.strip()
                if not href:
                    continue
                full_src = urljoin(url, href)
                image_urls.append(full_src)

            # 혹시 a가 없고 img만 있는 경우 대비: img src 사용
            img_els = page.locator("div.vc_column-inner img[src*='wp-content/uploads']")
            for idx in range(img_els.count()):
                src = img_els.nth(idx).get_attribute("src")
                if not src:
                    continue
                src = src.strip()
                if not src:
                    continue
                full_src = urljoin(url, src)
                image_urls.append(full_src)

            # 중복 제거
            image_urls = list(dict.fromkeys(image_urls))

            ex["author"] = artist
            ex["description"] = description
            ex["img_url"] = image_urls

            print(f"[상세] 이미지 개수: {len(image_urls)}, 설명 길이: {len(description)}")

        browser.close()
        print(f"\n[최종] RHO GALLERY 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조 (기존과 동일 가정):

      id           BIGINT PK (auto increment)
      title        VARCHAR(...) NOT NULL
      description  VARCHAR(...) NOT NULL
      address      VARCHAR(...)
      author       VARCHAR(...) NOT NULL
      start_date   DATE
      end_date     DATE NOT NULL
      open_time    TIME
      close_time   TIME
      views        INTEGER NOT NULL
      img_url      VARCHAR(255)[] NOT NULL
      gallery_name VARCHAR(...)
      phone_num    VARCHAR(...)
      created_at   DATE NOT NULL
      modified_at  DATE
    """
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")
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

        for ex in exhibitions:
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            # end_date NOT NULL이면 없는 건 스킵
            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                continue

            # 시간 파싱 실패 시 기본값으로 대체
            open_t = to_time_or_none(ex.get("open_time")) or DEFAULT_OPEN_TIME
            close_t = to_time_or_none(ex.get("close_time")) or DEFAULT_CLOSE_TIME

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    ex.get("description") or "",
                    ex.get("address"),
                    ex.get("author") or "",
                    start_dt,
                    end_dt,
                    open_t,
                    close_t,
                    0,                         # views 기본값 0
                    ex.get("img_url", []),     # img_url: 배열 컬럼 가정
                    ex.get("gallery_name"),
                    None,                      # phone_num: 아직 없음
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
    output_path = "rhoGallery.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

     # 2) DB에 저장
    save_to_postgres(data)

