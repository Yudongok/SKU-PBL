import time
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime, date
import os
import psycopg2  # ✅ DB 저장 추가

# 마루아트센터 현재 전시 URL
LIST_URL = "https://maruartcenter.co.kr/default/exhibit/exhibit01.php?sub=01"


# ==============================
# 공백 제거 유틸 (✅ 추가)
# ==============================
def normalize_text(s: str) -> str:
    """None 또는 공백만 있는 텍스트를 빈 문자열로 정리"""
    if not s:
        return ""
    return s.strip()


# ==============================
# 날짜 변환 유틸 (✅ DB용 추가)
# ==============================
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
    """'AM 10:30' / 'PM 18:30' / '10:30' 등을 TIME으로 최대한 변환"""
    if not s:
        return None
    s = s.strip()

    # 'AM 10:30' / 'PM 18:30' 형태면 시간만 뽑기
    m = re.search(r"(\d{1,2}:\d{2})", s)
    if m:
        hhmm = m.group(1)
    else:
        return None

    try:
        return datetime.strptime(hhmm, "%H:%M").time()
    except ValueError:
        return None


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.12.3', '12.8', '8' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    s = part.strip()

    # 1) YYYY.MM.DD 형식인지 확인
    m = re.match(r"^\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # 2) MM.DD 형식
    if base_date:
        m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s*$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

    # 3) DD 형식
    if base_date:
        m = re.match(r"^\s*(\d{1,2})\s*$", s)
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
      '2025.12.3-12.8'      -> ('2025-12-03', '2025-12-08')
      '2025.12.3~12.8'      -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 2025.12.8' -> ('2025-12-03', '2025-12-08')
    실패 시: (원본 문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    parts = re.split(r"\s*[~-]\s*", text)
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
    예시: 'AM 10:30 ~ PM 18:30(연중무휴)'
    -> ('AM 10:30', 'PM 18:30')
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


# ==============================
# 크롤러
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 리스트 페이지 수집
        print(">>> [1단계] 리스트 페이지 접속 중...")
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []

        title_spans = page.locator("span.gallery_title")
        count = title_spans.count()
        print(f"[리스트] 발견된 전시 개수: {count}")

        for i in range(count):
            title_span = title_spans.nth(i)

            raw_title = title_span.inner_text()
            title_kr = " ".join(raw_title.split())

            link_el = title_span.locator("xpath=./ancestor::a[1]")
            href = link_el.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            title_row = title_span.locator("xpath=./ancestor::tr[1]")
            date_row = title_row.locator("xpath=./following-sibling::tr[1]")

            operating_day = ""
            if date_row.count():
                raw_date = date_row.inner_text().strip()
                operating_day = (
                    raw_date.replace("[", "")
                    .replace("]", "")
                    .replace("기간 :", "")
                    .strip()
                )

            start_date, end_date = parse_operating_day(operating_day)

            operating_hour = "AM 10:30 ~ PM 18:30(연중무휴)"
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "title": title_kr,
                    "start_date": start_date,
                    "end_date": end_date,
                    "galleryName": "마루아트센터",
                    "open_time": open_time,
                    "close_time": close_time,
                    "detailUrl": detail_url,
                    "address": "",
                    "description": "",
                    "imageUrl": [],
                }
            )

        print(f"[리스트] 총 {len(exhibitions)}개 수집 완료. 상세 페이지 크롤링 시작...\n")

        # 2) 상세 페이지 순회
        for i, ex in enumerate(exhibitions):
            url = ex["detailUrl"]
            print(f"[{i+1}/{len(exhibitions)}] 상세 이동: {ex['title']}")

            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  -> 접속 실패: {e}")
                continue

            # (1) 이미지 수집
            image_urls = []
            imgs = page.locator("#post_area img").all()
            if not imgs:
                imgs = page.locator("div[style*='text-align: center'] img").all()

            for img in imgs:
                src = img.get_attribute("src")
                if src and "u_image" in src:
                    full_url = urljoin(url, src)
                    image_urls.append(full_url)

            ex["imageUrl"] = list(dict.fromkeys(image_urls))
            print(f"  -> 이미지: {len(ex['imageUrl'])}개 발견")

            # (2) 텍스트 분석
            post_area = page.locator("#post_area")
            if post_area.count():
                p_texts = post_area.locator("p, div").all_inner_texts()
            else:
                p_texts = page.locator("body").all_inner_texts()

            location_text = "마루아트센터"
            desc_lines = []
            is_note = False

            for text in p_texts:
                clean = text.strip()
                if not clean:
                    continue

                # 위치 찾기
                if ("마루아트센터" in clean or "관" in clean) and not is_note:
                    if len(clean) < 50:
                        location_text = clean

                # 작가노트/작품설명 시작
                if "[작가노트]" in clean or "[작품설명]" in clean or "[작품 설명]" in clean:
                    is_note = True
                    continue

                if is_note:
                    desc_lines.append(clean)

            ex["address"] = location_text
            ex["description"] = "\n".join(desc_lines).strip()

        browser.close()

        # detailUrl는 최종 JSON에는 제외
        for ex in exhibitions:
            if "detailUrl" in ex:
                del ex["detailUrl"]

        return exhibitions


# ==============================
# ✅ DB 저장 함수 추가
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조(다른 갤러리 코드와 동일 가정)
    """
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "api.insa-exhibition.shop")  # 필요 시 변경
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
            # ✅ description 비어있으면 스킵
            desc = normalize_text(ex.get("description") or "")
            if not desc:
                print(f"[DB] description 없음, 스킵: {ex.get('title')}")
                continue

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
                    desc,
                    ex.get("address"),
                    "",  # author 없음이면 빈 문자열
                    start_dt,
                    end_dt,
                    open_t,
                    close_t,
                    0,
                    ex.get("imageUrl", []),  # img_url 컬럼에 배열로 넣기
                    ex.get("galleryName") or "마루아트센터",
                    None,
                    today,
                    None,
                ),
            )

        conn.commit()
        print(f"[DB] INSERT 시도 완료 (description 없는 건 제외)")

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

    if data is not None:
        output_path = "maruArtCenter.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("\n========= 저장 완료 =========")
        print(f"파일 위치: {output_path}")
        print(f"총 데이터 개수: {len(data)}")

        # ✅ DB 저장 실행
        save_to_postgres(data)

    else:
        print("데이터 수집 실패")
