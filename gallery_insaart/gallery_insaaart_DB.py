from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime, date, time
import os

import psycopg2  # PostgreSQL 연동용
# from psycopg2.extras import Json  # 지금은 안 씀 (imageUrl을 배열로 저장한다고 가정)


# 갤러리 인사아트의 현재 전시 url
LIST_URL = "https://galleryinsaart.com/exhibitions-current/"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part 예시: '2025.12.3', '2025 11/26', '12/8', '11. 26' 등
    base_date: 연도가 없을 때 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()

    # "11/26" -> "11.26", "2025 11" -> "2025.11", 중복 점 제거
    s = s.replace('/', '.')
    s = re.sub(r"\s+", ".", s)
    s = re.sub(r"\.+", ".", s)

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
    '2025 11/26 - 12/08' 처럼 슬래시나 공백이 섞여도 처리
    성공 시: ('YYYY-MM-DD', 'YYYY-MM-DD')
    실패 시: (원본 문자열 또는 '', '')
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # -, ~, – 등의 구분자로 시작/종료일 분리
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)

    if len(parts) != 2:
        # 하나짜리 날짜인 경우 시도
        dt = parse_single_date(text)
        if dt:
            return dt.strftime("%Y-%m-%d"), ""
        return text, ""

    start_part, end_part = parts[0], parts[1]

    # 시작일
    start_dt = parse_single_date(start_part)
    if not start_dt:
        return text, ""

    # 종료일 (연/월 유추)
    end_dt = parse_single_date(end_part, base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(operating_hour: str):
    """
    예시: 'AM 10:00 ~ PM 19:00' 또는 '10:00 ~ 18:00'
    -> ('10:00', '19:00') 처럼 HH:MM 형식의 문자열로 반환
    (PostgreSQL TIME 컬럼에 넣기 좋게)
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
    """'YYYY-MM-DD' 형식 문자열을 date 객체로 변환, 아니면 None"""
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
    """'HH:MM' 형식 문자열을 time 객체로 변환, 아니면 None"""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return None


# ==============================
# 크롤러 본체
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 현재전시 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        detail_urls = []

        # ▶ 전시 제목(h4 안의 a) 기준으로 목록 수집
        h4_links = page.locator("h4 a")
        count = h4_links.count()
        print(f"[리스트] 전시 개수(h4 a): {count}")

        for i in range(count):
            link = h4_links.nth(i)
            title_kr = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # 전시장(h3)
            h4 = link.locator("xpath=ancestor::h4[1]")
            section_loc = h4.locator("xpath=preceding::h3[1]")
            section = section_loc.inner_text().strip() if section_loc.count() else ""

            # 전시 기간 (p[2])
            date_loc = h4.locator("xpath=following-sibling::p[2]")
            date_text = date_loc.inner_text().strip() if date_loc.count() else ""

            # 운영 시간 (raw)
            operating_hour = "AM 10:00 ~ PM 19:00"

            # 날짜/시간 파싱
            start_date, end_date = parse_operating_day(date_text)
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "address": section,
                    "title": title_kr,
                    "start_date": start_date,     # 'YYYY-MM-DD' 또는 ''
                    "end_date": end_date,         # 'YYYY-MM-DD' 또는 ''
                    "open_time": open_time,       # 'HH:MM' 또는 ''
                    "close_time": close_time,     # 'HH:MM' 또는 ''
                    "gallery_name": "갤러리인사아트",
                    "author": "",                 # 아래에서 채움
                    "description": "",
                    "img_url": [],               # 아래에서 채움
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

            # 작가 정보
            artist = ""
            h5s = page.locator("h5, h6")
            h5_count = h5s.count()
            if h5_count >= 1:
                artist = h5s.nth(0).inner_text().strip()

            # 설명 텍스트
            description = ""
            text_container = page.locator("div.fusion-text.fusion-text-2")
            if text_container.count():
                paragraphs = text_container.locator("p").all_inner_texts()
            else:
                paragraphs = page.locator("p").all_inner_texts()

            description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # 이미지 URL들
            img_elements = page.locator("img")
            img_count = img_elements.count()
            image_urls = []
            for idx in range(img_count):
                src = img_elements.nth(idx).get_attribute("src") or ""
                src = src.strip()
                if not src:
                    continue
                if "wp-content/uploads/2025/" not in src:
                    continue
                image_urls.append(src)

            # 중복 제거
            image_urls = list(dict.fromkeys(image_urls))

            ex["author"] = artist
            ex["description"] = description
            ex["img_url"] = image_urls

            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조(가정):

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
      imageUrl     VARCHAR(255)[] NOT NULL   -- 배열 타입이라고 가정
      galleryName  VARCHAR(...)
      phoneNum     VARCHAR(...)
      createdAt    DATE NOT NULL
      modifiedAt   DATE
    """
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")  # 필요 시 localhost 등으로 변경
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
            # 날짜 문자열 -> date 객체로 변환
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            # end_date는 NOT NULL 가정 → 없으면 스킵
            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                continue

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",          # title (NOT NULL)
                    ex.get("description") or "",    # description (NOT NULL)
                    ex.get("address"),              # address
                    ex.get("author") or "",         # author (NOT NULL)
                    start_dt,                       # start_date
                    end_dt,                         # end_date (NOT NULL)
                    open_t,                         # open_time
                    close_t,                        # close_time
                    0,                              # views 초기값
                    ex.get("img_url", []),         # imageUrl: 배열 컬럼 가정
                    ex.get("gallery_name"),
                    None,                           # phoneNum: 지금은 없음
                    today,                          # createdAt
                    None,                           # modifiedAt
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

    # 1) Json파일로 저장
    output_path = "gallery_insaart.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

    # 2) DB에 저장
    save_to_postgres(data)
