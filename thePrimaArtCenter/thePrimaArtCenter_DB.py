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

# 프리마아트센터 현재 전시 URL
LIST_URL = "https://primaartcenter.co.kr/kor/exhibition/list.html?state=current"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 26', '2025.12.3', '12.8', '8', '2025-08-25' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜 (datetime 또는 None)
    """
    if not part:
        return None

    s = part.strip()

    # 0) YYYY-MM-DD 형식 우선 처리
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
      '2025-08-25 - 2026-05-31'       -> ('2025-08-25', '2026-05-31')   # 프리마
      '2025. 11. 26 - 2025. 12. 15'  -> ('2025-11-26', '2025-12-15')
      '2025.12.3-12.8'               -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 12.8'             -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 2025.12.8'        -> ('2025-12-03', '2025-12-08')
    실패 시: (원본 문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 1) 먼저 YYYY-MM-DD - YYYY-MM-DD 패턴 시도 (프리마 현재 구조)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", text)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            # 형식이 맞는데 ValueError 나면 아래 일반 로직으로 다시 시도
            pass

    # 2) 그 외 경우: ~, -, – 기준으로 앞/뒤 나누기해서 dot 형식 등 처리
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
    예시: '10:30 ~ 19:30 (입장마감 19:00)'
    -> ('10:30', '19:30')  # HH:MM 형식으로 반환 (TIME 컬럼용)
    """
    if not operating_hour:
        return "", ""

    # 괄호 뒤 설명 제거
    base = operating_hour.split("(", 1)[0].strip()
    # HH:MM 패턴만 뽑기
    times = re.findall(r"\d{1,2}:\d{2}", base)

    if len(times) >= 2:
        return times[0], times[1]
    elif len(times) == 1:
        return times[0], ""
    else:
        return "", ""


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
# 크롤러 본체 (프리마아트센터)
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

        # ▶ 각 전시 블록
        # <div class="item_wrap item_wrap_02 hover_line_action"> ... </div>
        items = page.locator("div.item_wrap.item_wrap_02.hover_line_action")
        count = items.count()
        print(f"[리스트] 전시 개수(프리마): {count}")

        if count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. item_wrap 구조를 다시 확인해 주세요.")

        for i in range(count):
            item = items.nth(i)

            # (1) 제목 + 상세 페이지 URL
            link = item.locator("a.btn")
            if not link.count():
                continue

            # 제목은 <dt class="hover_line"> 안에 있음
            title_el = item.locator("div.info-book dl dt")
            if title_el.count():
                raw_title = title_el.first.inner_text()
            else:
                raw_title = link.inner_text()

            title_kr = " ".join(raw_title.split())

            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (2) 기간 텍스트: <dd>에 "2025-08-25 - 2026-05-31" 형식
            operating_day_el = item.locator("div.info-book dl dd")
            if operating_day_el.count():
                operating_day = operating_day_el.first.inner_text().strip()
            else:
                operating_day = ""

            # operating_day → start_date / end_date
            start_date, end_date = parse_operating_day(operating_day)

            # (3) 전시장 / 주소
            # 리스트에서는 일단 기본값 사용, 상세 페이지에서 전시장소로 덮어쓸 것
            hall = "서울 종로구 인사동길 37-11 더프리마아트센터"
            gallery_txt = "더프리마아트센터"

            # (4) 운영시간 고정값 (프리마아트센터)
            operating_hour = "10:30 ~ 19:30 (입장마감 19:00)"
            open_time, close_time = parse_operating_hour(operating_hour)

            # (5) 리스트 썸네일 이미지 (img_wrap 안의 img)
            thumb_urls = []
            thumb_img = item.locator(".img_wrap img")
            if thumb_img.count():
                src = thumb_img.first.get_attribute("src")
                if src:
                    src = src.strip()
                    if src:
                        thumb_urls.append(urljoin(LIST_URL, src))

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": hall,
                    "gallery_name": gallery_txt,
                    "open_time": open_time,
                    "close_time": close_time,
                    "author": "",              # 작가 이름 (상세에서 채움)
                    "description": "",
                    "img_url": thumb_urls,     # 리스트 썸네일 우선 넣어둠
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

            # (0) 상세 상단 정보에서 '전시장소' 찾아서 address 덮어쓰기
            hall_detail = None
            info_items = page.locator("ul li")
            info_count = info_items.count()

            for k in range(info_count):
                li = info_items.nth(k)
                dt_el = li.locator("dt")
                dd_el = li.locator("dd")
                if not dt_el.count() or not dd_el.count():
                    continue

                dt_text = dt_el.first.inner_text().strip()
                dd_text = dd_el.first.inner_text().strip()

                if "전시장소" in dt_text:
                    hall_detail = dd_text
                    break

            if hall_detail:
                # 예: "더프리마 뮤지엄 B1"
                ex["address"] = hall_detail

            # (1) 작가 정보 (테이블 구조 가정, 없으면 빈 문자열)
            artist = ""
            rows = page.locator("tr")
            row_count = rows.count()

            for j in range(row_count):
                row = rows.nth(j)
                cells = row.locator("th, td")
                if cells.count() < 2:
                    continue

                label = cells.nth(0).inner_text().strip()
                if "작가" in label:
                    artist = "".join(cells.nth(1).inner_text().split())
                    break

            # (2) 설명 텍스트
            #  - div.detail.bar 우선 사용
            #  - 없으면 기존에 사용하던 컨테이너들로 fallback
            content = page.locator("div.detail.bar")
            if not content.count():
                content = page.locator(
                    "div.exhibition_view, div.view, div.view_cont, #bo_v_con, .bo_v_con"
                )
            if not content.count():
                content = page.locator("article")

            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = p_loc.all_inner_texts()
                else:
                    paragraphs = [content.inner_text()]
            else:
                paragraphs = page.locator("p").all_inner_texts()

            # 줄 단위 정리
            lines = [p.strip() for p in paragraphs if p.strip()]

            # 푸터/주소/전화번호/COPYRIGHT 라인 제거
            footer_prefixes = (
                "더프리마아트센터",
                "서울특별시 종로구 인사동길",
                "서울 종로구 인사동길",
                "TEL",
                "COPYRIGHT",
            )

            cleaned_lines = []
            for ln in "\n".join(lines).splitlines():
                s = ln.strip()
                if any(s.startswith(prefix) for prefix in footer_prefixes):
                    # 이 줄부터는 하단 푸터로 보고 버림
                    break
                cleaned_lines.append(s)

            description = "\n".join(cleaned_lines).strip()

            # 혹시 다 잘려서 아무 것도 안 남았으면, 그냥 원본 사용
            if not description and lines:
                description = "\n".join(lines).strip()

            # (3) 이미지 URL
            #  - 리스트에서 가져온 썸네일을 기본으로 두고
            #  - 상세 페이지 전체 <img> 중 upload/board 포함된 것들 추가
            image_urls = list(ex.get("img_url", []))  # 이미 넣어둔 썸네일 복사

            img_els = page.locator("img")
            img_count = img_els.count()

            for idx in range(img_count):
                img_el = img_els.nth(idx)
                src = img_el.get_attribute("src")
                if not src:
                    continue
                src = src.strip()
                if not src:
                    continue

                # 프리마 사이트 실제 이미지 경로만 필터링
                if "upload/board" not in src:
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
        print(f"\n[최종] 프리마 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조(인사아트센터 코드와 동일 가정):

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

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

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
    output_path = "primaArt.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")
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

# 프리마아트센터 현재 전시 URL
LIST_URL = "https://primaartcenter.co.kr/kor/exhibition/list.html?state=current"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 26', '2025.12.3', '12.8', '8', '2025-08-25' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜 (datetime 또는 None)
    """
    if not part:
        return None

    s = part.strip()

    # 0) YYYY-MM-DD 형식 우선 처리
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
      '2025-08-25 - 2026-05-31'       -> ('2025-08-25', '2026-05-31')   # 프리마
      '2025. 11. 26 - 2025. 12. 15'  -> ('2025-11-26', '2025-12-15')
      '2025.12.3-12.8'               -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 12.8'             -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 2025.12.8'        -> ('2025-12-03', '2025-12-08')
    실패 시: (원본 문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 1) 먼저 YYYY-MM-DD - YYYY-MM-DD 패턴 시도 (프리마 현재 구조)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", text)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            # 형식이 맞는데 ValueError 나면 아래 일반 로직으로 다시 시도
            pass

    # 2) 그 외 경우: ~, -, – 기준으로 앞/뒤 나누기해서 dot 형식 등 처리
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
    예시: '10:30 ~ 19:30 (입장마감 19:00)'
    -> ('10:30', '19:30')  # HH:MM 형식으로 반환 (TIME 컬럼용)
    """
    if not operating_hour:
        return "", ""

    # 괄호 뒤 설명 제거
    base = operating_hour.split("(", 1)[0].strip()
    # HH:MM 패턴만 뽑기
    times = re.findall(r"\d{1,2}:\d{2}", base)

    if len(times) >= 2:
        return times[0], times[1]
    elif len(times) == 1:
        return times[0], ""
    else:
        return "", ""


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
# 크롤러 본체 (프리마아트센터)
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

        # ▶ 각 전시 블록
        # <div class="item_wrap item_wrap_02 hover_line_action"> ... </div>
        items = page.locator("div.item_wrap.item_wrap_02.hover_line_action")
        count = items.count()
        print(f"[리스트] 전시 개수(프리마): {count}")

        if count == 0:
            print("[리스트] 전시 아이템을 찾지 못했습니다. item_wrap 구조를 다시 확인해 주세요.")

        for i in range(count):
            item = items.nth(i)

            # (1) 제목 + 상세 페이지 URL
            link = item.locator("a.btn")
            if not link.count():
                continue

            # 제목은 <dt class="hover_line"> 안에 있음
            title_el = item.locator("div.info-book dl dt")
            if title_el.count():
                raw_title = title_el.first.inner_text()
            else:
                raw_title = link.inner_text()

            title_kr = " ".join(raw_title.split())

            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (2) 기간 텍스트: <dd>에 "2025-08-25 - 2026-05-31" 형식
            operating_day_el = item.locator("div.info-book dl dd")
            if operating_day_el.count():
                operating_day = operating_day_el.first.inner_text().strip()
            else:
                operating_day = ""

            # operating_day → start_date / end_date
            start_date, end_date = parse_operating_day(operating_day)

            # (3) 전시장 / 주소
            # 리스트에서는 일단 기본값 사용, 상세 페이지에서 전시장소로 덮어쓸 것
            hall = "서울 종로구 인사동길 37-11 더프리마아트센터"
            gallery_txt = "더프리마아트센터"

            # (4) 운영시간 고정값 (프리마아트센터)
            operating_hour = "10:30 ~ 19:30 (입장마감 19:00)"
            open_time, close_time = parse_operating_hour(operating_hour)

            # (5) 리스트 썸네일 이미지 (img_wrap 안의 img)
            thumb_urls = []
            thumb_img = item.locator(".img_wrap img")
            if thumb_img.count():
                src = thumb_img.first.get_attribute("src")
                if src:
                    src = src.strip()
                    if src:
                        thumb_urls.append(urljoin(LIST_URL, src))

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "address": hall,
                    "gallery_name": gallery_txt,
                    "open_time": open_time,
                    "close_time": close_time,
                    "author": "",              # 작가 이름 (상세에서 채움)
                    "description": "",
                    "img_url": thumb_urls,     # 리스트 썸네일 우선 넣어둠
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

            # (0) 상세 상단 정보에서 '전시장소' 찾아서 address 덮어쓰기
            hall_detail = None
            info_items = page.locator("ul li")
            info_count = info_items.count()

            for k in range(info_count):
                li = info_items.nth(k)
                dt_el = li.locator("dt")
                dd_el = li.locator("dd")
                if not dt_el.count() or not dd_el.count():
                    continue

                dt_text = dt_el.first.inner_text().strip()
                dd_text = dd_el.first.inner_text().strip()

                if "전시장소" in dt_text:
                    hall_detail = dd_text
                    break

            if hall_detail:
                # 예: "더프리마 뮤지엄 B1"
                ex["address"] = hall_detail

            # (1) 작가 정보 (테이블 구조 가정, 없으면 빈 문자열)
            artist = ""
            rows = page.locator("tr")
            row_count = rows.count()

            for j in range(row_count):
                row = rows.nth(j)
                cells = row.locator("th, td")
                if cells.count() < 2:
                    continue

                label = cells.nth(0).inner_text().strip()
                if "작가" in label:
                    artist = "".join(cells.nth(1).inner_text().split())
                    break

            # (2) 설명 텍스트
            #  - div.detail.bar 우선 사용
            #  - 없으면 기존에 사용하던 컨테이너들로 fallback
            content = page.locator("div.detail.bar")
            if not content.count():
                content = page.locator(
                    "div.exhibition_view, div.view, div.view_cont, #bo_v_con, .bo_v_con"
                )
            if not content.count():
                content = page.locator("article")

            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = p_loc.all_inner_texts()
                else:
                    paragraphs = [content.inner_text()]
            else:
                paragraphs = page.locator("p").all_inner_texts()

            # 줄 단위 정리
            lines = [p.strip() for p in paragraphs if p.strip()]

            # 푸터/주소/전화번호/COPYRIGHT 라인 제거
            footer_prefixes = (
                "더프리마아트센터",
                "서울특별시 종로구 인사동길",
                "서울 종로구 인사동길",
                "TEL",
                "COPYRIGHT",
            )

            cleaned_lines = []
            for ln in "\n".join(lines).splitlines():
                s = ln.strip()
                if any(s.startswith(prefix) for prefix in footer_prefixes):
                    # 이 줄부터는 하단 푸터로 보고 버림
                    break
                cleaned_lines.append(s)

            description = "\n".join(cleaned_lines).strip()

            # 혹시 다 잘려서 아무 것도 안 남았으면, 그냥 원본 사용
            if not description and lines:
                description = "\n".join(lines).strip()

            # (3) 이미지 URL
            #  - 리스트에서 가져온 썸네일을 기본으로 두고
            #  - 상세 페이지 전체 <img> 중 upload/board 포함된 것들 추가
            image_urls = list(ex.get("img_url", []))  # 이미 넣어둔 썸네일 복사

            img_els = page.locator("img")
            img_count = img_els.count()

            for idx in range(img_count):
                img_el = img_els.nth(idx)
                src = img_el.get_attribute("src")
                if not src:
                    continue
                src = src.strip()
                if not src:
                    continue

                # 프리마 사이트 실제 이미지 경로만 필터링
                if "upload/board" not in src:
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
        print(f"\n[최종] 프리마 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조(인사아트센터 코드와 동일 가정):

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

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

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
    output_path = "primaArt.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")

    # 2) DB에 저장
    save_to_postgres(data)
