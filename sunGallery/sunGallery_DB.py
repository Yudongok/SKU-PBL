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

LIST_URL = "https://www.sungallery.co.kr/exhibitions/current/"
GALLERY_NAME = "선화랑"
GALLERY_ADDRESS = "서울 종로구 인사동5길 8 선화랑"
DEFAULT_OPEN_TIME_STR = "10:00"
DEFAULT_CLOSE_TIME_STR = "18:00"

# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def month_str_to_int(mon: str) -> int | None:
    if not mon:
        return None
    mon3 = mon.strip()[:3].upper()
    mapping = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    return mapping.get(mon3)

def parse_single_date(part, base_date=None):
    if not part:
        return None
    s = part.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    s = re.sub(r"\s*\.\s*", ".", s)
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    if base_date:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

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
    if not operating_day:
        return "", ""
    text = operating_day.strip()

    # 1) "3 Dec 2025 - 13 Jan 2026"
    m = re.match(
        r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$",
        text
    )
    if m:
        d1, mon1, y1, d2, mon2, y2 = m.groups()
        m1, m2 = month_str_to_int(mon1), month_str_to_int(mon2)
        if m1 and m2:
            try:
                dt1 = datetime(int(y1), m1, int(d1))
                dt2 = datetime(int(y2), m2, int(d2))
                return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2) "3 - 31 Dec 2025"
    m = re.match(r"^(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$", text)
    if m:
        d1, d2, mon, y = m.groups()
        mth = month_str_to_int(mon)
        if mth:
            try:
                dt1 = datetime(int(y), mth, int(d1))
                dt2 = datetime(int(y), mth, int(d2))
                return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3) YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-~–]\s*(\d{4}-\d{2}-\d{2})$", text)
    if m:
        s1, s2 = m.groups()
        try:
            dt1 = datetime.strptime(s1, "%Y-%m-%d")
            dt2 = datetime.strptime(s2, "%Y-%m-%d")
            return dt1.strftime("%Y-%m-%d"), dt2.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 4) Fallback
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        return "", ""
    start_dt = parse_single_date(parts[0])
    if not start_dt:
        return "", ""
    end_dt = parse_single_date(parts[1], base_date=start_dt)
    if not end_dt:
        return start_dt.strftime("%Y-%m-%d"), ""
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")

def parse_operating_hour(operating_hour: str):
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
    if not s or len(s.strip()) != 10:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

def to_time_or_none(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%H:%M").time()
    except ValueError:
        return None

# ==============================
# 크롤러 본체 (선화랑)
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        detail_urls = []

        items = page.locator("ul.clearwithin > li")
        count = items.count()
        print(f"[리스트] 항목 개수(선화랑): {count}")

        for i in range(count):
            item = items.nth(i)
            a_tag = item.locator("a").first

            if not a_tag.count():
                continue

            href = a_tag.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            clean_url = detail_url.rstrip("/")
            if clean_url.endswith("/exhibitions/current") or \
               clean_url.endswith("/exhibitions/past") or \
               clean_url.endswith("/exhibitions"):
                print(f"[스킵] 목록/네비게이션 페이지: {detail_url}")
                continue

            title_el = item.locator("div.content h2")
            title = title_el.inner_text().strip() if title_el.count() else ""

            if not title:
                print(f"[스킵] 제목 없음(유효한 전시 아님): {detail_url}")
                continue

            artist_el = item.locator("div.content span.subtitle")
            author = artist_el.inner_text().strip() if artist_el.count() else ""

            date_el = item.locator("div.content span.date")
            date_text = date_el.inner_text().strip() if date_el.count() else ""
            start_date, end_date = parse_operating_day(date_text)

            desc_el = item.locator("div.content span.description")
            short_desc = desc_el.inner_text().strip() if desc_el.count() else ""

            img_el = item.locator("span.image img")
            img_url = ""
            if img_el.count():
                src = img_el.get_attribute("data-src") or img_el.get_attribute("src")
                if src:
                    img_url = src.strip()

            operating_hour = f"{DEFAULT_OPEN_TIME_STR} ~ {DEFAULT_CLOSE_TIME_STR}"
            open_time_str, close_time_str = parse_operating_hour(operating_hour)

            exhibitions.append({
                "title": title,
                "start_date": start_date,
                "end_date": end_date,
                "address": GALLERY_ADDRESS,
                "gallery_name": GALLERY_NAME,
                "open_time": open_time_str,
                "close_time": close_time_str,
                "author": author,
                "description": short_desc,
                "img_url": [img_url] if img_url else [],
            })
            detail_urls.append(detail_url)

        print(f"[리스트] 최종 수집된 전시 수: {len(exhibitions)}")

        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            content = page.locator("div.prose .description")
            if not content.count():
                content = page.locator("div.exhibition-detail")
            if not content.count():
                content = page.locator("div.body")

            description = ex.get("description", "")

            if content.count():
                p_loc = content.locator("p")
                paragraphs = []
                all_p_texts = p_loc.all_inner_texts()

                if not all_p_texts:
                    raw_text = content.inner_text().strip()
                    if re.search(r'[가-힣]', raw_text):
                        paragraphs.append(raw_text)
                else:
                    for txt in all_p_texts:
                        t = txt.strip()
                        if not t:
                            continue
                        if re.search(r'[가-힣]', t):
                            paragraphs.append(t)

                full_desc = "\n\n".join(paragraphs).strip()

                if full_desc and len(full_desc) > len(description):
                    description = full_desc
                    print(f"[상세] 한글 설명 수집 성공 (길이: {len(description)})")
                else:
                    print("[상세] 유효한 한글 설명 없음 (기존 설명 유지)")

            image_urls = ex.get("img_url", [])[:]
            img_els_detail = page.locator("img")
            img_count = img_els_detail.count()

            for idx in range(img_count):
                img_el = img_els_detail.nth(idx)
                src = img_el.get_attribute("src")
                if not src:
                    continue
                src = src.strip()

                if "artlogic-res.cloudinary.com" in src and "/images/exhibitions/" in src:
                    full_src = src
                else:
                    full_src = urljoin(url, src)
                image_urls.append(full_src)

            image_urls = list(dict.fromkeys(image_urls))

            ex["description"] = description
            ex["img_url"] = image_urls

        browser.close()
        print(f"\n[최종] 선화랑 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions

# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
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
        default_open_t = to_time_or_none(DEFAULT_OPEN_TIME_STR) or time(10, 0)
        default_close_t = to_time_or_none(DEFAULT_CLOSE_TIME_STR) or time(18, 0)

        saved = 0
        skipped = 0

        for ex in exhibitions:
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                skipped += 1
                continue

            # ✅ description 비어있으면 저장하지 않음
            desc = (ex.get("description") or "").strip()
            if not desc:
                print(f"[DB] description 없음, 스킵: {ex.get('title')}")
                skipped += 1
                continue

            open_t = to_time_or_none(ex.get("open_time")) or default_open_t
            close_t = to_time_or_none(ex.get("close_time")) or default_close_t

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    desc,  # ✅ 정리된 description 저장
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

# ==============================
# 메인 실행부
# ==============================

if __name__ == "__main__":
    data = crawl_exhibitions()

    output_path = "sunGallery.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")

    save_to_postgres(data)
    print("========= 저장 완료 =========")
