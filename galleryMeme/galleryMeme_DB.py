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

LIST_URL = "http://www.gallerymeme.com/web/current.html"

GALLERY_NAME = "갤러리밈"
BASE_ADDRESS = "갤러리밈"  # 필요하면 실제 주소로 교체

# 없을 때 쓸 기본 운영 시간
DEFAULT_OPEN_TIME = time(10, 30)   # 10:30
DEFAULT_CLOSE_TIME = time(18, 30)  # 18:30


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025.11.12.', '2025. 11. 26', '2025.12.3', '12.8', '8', '2025-08-25' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜 (datetime 또는 None)
    """
    if not part:
        return None

    s = part.strip()
    # '2025.11.12.' 처럼 끝에 점 찍힌 경우 제거
    s = s.rstrip(".")

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
      '2025.11.12. ~ 2025.12.21.'
      '2025-01-05 ~ 2025-02-03'
      '2025. 1. 5 - 2025. 2. 3'
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 0) 문자열에서 YYYY-MM-DD 2개 뽑기 (있으면 바로 사용)
    found = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text)
    if len(found) >= 2:
        dt1 = parse_single_date(found[0])
        dt2 = parse_single_date(found[1])
        if dt1 and dt2:
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")

    # 1) ~, -, – 기준으로 나누기
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
    예시: '10:30 ~ 18:30'
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
# 크롤러 본체 (갤러리밈)
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

        # 리스트: exbView 상세로 가는 a 태그들
        items = page.locator("a[href*='exbView']")
        raw_count = items.count()
        print(f"[리스트] 전시 a태그 개수(갤러리밈): {raw_count}")

        if raw_count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. a[href*='exbView'] 구조를 다시 확인해 주세요.")
            browser.close()
            return []

        seen_detail_urls = set()  # 중복 URL 제거용

        for i in range(raw_count):
            item = items.nth(i)

            # 우선 href / detail_url부터 구해서 중복 체크
            href = item.get_attribute("href") or ""
            href = href.strip()
            if not href:
                continue

            detail_url = urljoin(LIST_URL, href)

            if detail_url in seen_detail_urls:
                print(f"[리스트] 중복 URL, 스킵: {detail_url}")
                continue
            seen_detail_urls.add(detail_url)

            # (1) 제목: .cur_title
            title_el = item.locator(".cur_title")
            raw_title = title_el.inner_text() if title_el.count() else item.inner_text()
            title_kr = " ".join(raw_title.split())

            # (2) 작가: .cur_artist → author
            artist_el = item.locator(".cur_artist")
            artist = artist_el.inner_text().strip() if artist_el.count() else ""

            # (3) 전시장 정보: .cur_cate → address 에 저장
            cate_el = item.locator(".cur_cate")
            cate_text = cate_el.inner_text().strip() if cate_el.count() else ""
            # 층 정보 + 갤러리명 그대로 address에 넣기
            address = cate_text or BASE_ADDRESS

            # (4) 전시 기간: .cur_date
            date_el = item.locator(".cur_date")
            operating_day = date_el.inner_text().strip() if date_el.count() else ""
            start_date, end_date = parse_operating_day(operating_day)

            # (5) 리스트 썸네일 이미지: figure img
            thumb_urls = []
            img_el = item.locator("figure img")
            if img_el.count():
                src = img_el.first.get_attribute("src")
                if src:
                    src = src.strip()
                    if src:
                        thumb_urls.append(urljoin(LIST_URL, src))

            # (6) 운영시간: 페이지에 없으니 문자열은 빈 값, 실제 DB에는 기본값 넣을 예정
            operating_hour = ""
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": address,               # 전시장 층 정보
                    "gallery_name": GALLERY_NAME,
                    "open_time": "10:30",
                    "close_time": "18:00",
                    "author": artist,                 # 작가
                    "description": "",
                    "img_url": thumb_urls,            # 상세에서 추가로 붙일 수 있음
                }
            )
            detail_urls.append(detail_url)

        print(f"[리스트] 중복 제거 후 수집된 전시 수: {len(exhibitions)}")

        # 2) 각 전시별 상세 페이지 크롤링
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (1) 상세에서 작가 정보 다시 찾기 (있으면 갱신)
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

            # (2) 설명 텍스트: **class="0" 인 p 태그들만 사용 + 작가 이력 부분 잘라내기**
            paras0 = page.locator("p[class='0']")
            raw_paras = paras0.all_inner_texts() if paras0.count() else []
            raw_lines = [p.strip() for p in raw_paras if p.strip()]

            filtered_lines = []
            for line in raw_lines:
                l = line.strip()
                lower = l.lower()

                # 작가 이력/경력이 시작되는 신호들 → 여기서부터는 버림
                if re.match(r"^b\.\s*\d{4}", l):  # 예: "b. 1957"
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

            # (3) 상세 이미지들 (필요하면 /upload/gallery, /data/file 등으로 필터링)
            image_urls = list(ex.get("img_url", []))

            img_els = page.locator("img")
            for idx in range(img_els.count()):
                img_el = img_els.nth(idx)
                src = img_el.get_attribute("src")
                if not src:
                    continue
                src = src.strip()
                if not src:
                    continue
                # 갤러리 작품 이미지일 가능성이 높은 경로만 필터링
                if "/upload/" not in src and "/data/file" not in src:
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
        print(f"\n[최종] 갤러리밈 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조(다른 갤러리와 동일 가정):

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

            # 시간 파싱 실패 시 디폴트 시간 사용
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
    output_path = "galleryMeme.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

     # 2) DB에 저장
    save_to_postgres(data)

