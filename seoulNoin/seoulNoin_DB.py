from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime, date, time
import os
import psycopg2

# ==============================
# 기본 설정
# ==============================

# 크롤링할 단일 페이지 URL
TARGET_URL = "https://seoulnoin.or.kr/senior/space2.asp"

GALLERY_NAME = "서울노인복지센터 탑골미술관"
GALLERY_ADDRESS = "서울시 종로구 삼일대로 467 서울노인복지센터 1층"
DEFAULT_OPEN_TIME_STR = "10:00"
DEFAULT_CLOSE_TIME_STR = "18:00"

# ==============================
# 유틸리티 함수
# ==============================

def parse_date_range(text: str):
    """
    '2025-12-04 ~ 2025-12-16' 형식의 텍스트 파싱
    """
    if not text:
        return None, None
    
    # 공백 제거 및 정리
    text = text.replace("&nbsp;", " ").strip()
    
    # 정규식: YYYY-MM-DD ~ YYYY-MM-DD
    pattern = r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s*[-~]\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})"
    m = re.search(pattern, text)
    if m:
        y1, m1, d1, y2, m2, d2 = map(int, m.groups())
        try:
            dt1 = datetime(y1, m1, d1).strftime("%Y-%m-%d")
            dt2 = datetime(y2, m2, d2).strftime("%Y-%m-%d")
            return dt1, dt2
        except ValueError:
            pass
            
    return None, None

def to_time_or_none(s):
    if not s: return None
    try: return datetime.strptime(s, "%H:%M").time()
    except: return None

def to_date_or_none(s):
    if not s: return None
    try: return datetime.strptime(s, "%Y-%m-%d").date()
    except: return None

# ==============================
# 크롤러 본체
# ==============================

def crawl_seoulnoin_single_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 봇 탐지 우회용 User-Agent
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = context.new_page()

        print(f"[접속] {TARGET_URL}")
        try:
            page.goto(TARGET_URL, timeout=60000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"[에러] 페이지 접속 실패: {e}")
            return []

        # ---------------------------------------------------------
        # 1. 제목 파싱
        # ---------------------------------------------------------
        title_el = page.locator("p.fs30.bold.black")
        title = ""
        if title_el.count():
            title = title_el.inner_text().strip()
        else:
            print("  [경고] 제목 태그(p.fs30.bold.black) 미발견")

        # ---------------------------------------------------------
        # 2. 날짜 파싱
        # ---------------------------------------------------------
        date_el = page.locator(".smInfo1 li.point")
        start_date, end_date = None, None
        if date_el.count():
            date_text = date_el.inner_text().strip()
            start_date, end_date = parse_date_range(date_text)
        else:
            print("  [경고] 날짜 태그(.smInfo1 li.point) 미발견")

        # ---------------------------------------------------------
        # 3. 설명(Description) 파싱 (수정됨)
        # 요청사항: <div class="first_title">전시요약</div> 의 "다음 다음 div"
        # ---------------------------------------------------------
        description = ""
        
        # (1) "전시요약" 텍스트를 가진 div.first_title 찾기
        summary_title = page.locator("div.first_title", has_text="전시요약")
        
        if summary_title.count():
            # (2) XPath를 사용하여 해당 요소의 "다음 다음 형제 div" ([2])를 선택
            # following-sibling::div[2] -> 현재 요소 뒤에 나오는 div 중 2번째 것
            target_div = summary_title.locator("xpath=following-sibling::div[2]")
            
            if target_div.count():
                raw_text = target_div.inner_text()
                # 텍스트 정제 (불필요한 공백 제거)
                lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                description = "\n\n".join(lines)
            else:
                # 혹시 구조가 바뀌어서 '다음' div([1])에 있을 경우 대비 (Fallback)
                print("  [알림] '다음 다음' div가 없어 바로 '다음' div 확인")
                fallback_div = summary_title.locator("xpath=following-sibling::div[1]")
                if fallback_div.count():
                    description = fallback_div.inner_text().strip()
        else:
            print("  [경고] '전시요약' 타이틀을 찾지 못했습니다.")

        # ---------------------------------------------------------
        # 4. 이미지 파싱
        # ---------------------------------------------------------
        img_urls = []
        
        # (1) alt="전시이미지" 우선 검색
        target_img = page.locator("img[alt='전시이미지']")
        
        # (2) 없으면 upload 경로가 포함된 이미지 검색
        if not target_img.count():
            target_img = page.locator("img[src*='upload']")

        for i in range(target_img.count()):
            src = target_img.nth(i).get_attribute("src")
            if src:
                full_src = urljoin(TARGET_URL, src)
                img_urls.append(full_src)

        # 중복 제거
        img_urls = list(dict.fromkeys(img_urls))

        # ---------------------------------------------------------
        # 5. 결과 출력 및 데이터 생성
        # ---------------------------------------------------------
        print(f"  -> 제목: {title}")
        print(f"  -> 날짜: {start_date} ~ {end_date}")
        print(f"  -> 이미지: {len(img_urls)}개")
        print(f"  -> 설명 길이: {len(description)}")
        if len(description) > 0:
            print(f"  -> 설명(앞부분): {description[:50]}...")

        browser.close()

        if title and end_date:
            exhibition_data = {
                "title": title,
                "start_date": start_date,
                "end_date": end_date,
                "description": description,
                "img_url": img_urls,
                "address": GALLERY_ADDRESS,
                "gallery_name": GALLERY_NAME,
                "open_time": DEFAULT_OPEN_TIME_STR,
                "close_time": DEFAULT_CLOSE_TIME_STR,
                "author": "" 
            }
            return [exhibition_data]
        else:
            print("[실패] 필수 정보(제목 또는 날짜) 누락")
            return []

# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    db_user = os.getenv("POSTGRES_USER", "pbl")
    db_password = os.getenv("POSTGRES_PASSWORD", "1234")
    db_name = os.getenv("POSTGRES_DB", "pbl")
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")
    db_port = os.getenv("POSTGRES_PORT", "5432")

    conn = None
    try:
        conn = psycopg2.connect(
            dbname=db_name, user=db_user, password=db_password, host=db_host, port=db_port
        )
        cur = conn.cursor()

        insert_sql = """
        INSERT INTO exhibition
        (title, description, address, author, start_date, end_date,
         open_time, close_time, views, img_url, gallery_name, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
        """
        today = date.today()

        saved_count = 0
        for ex in exhibitions:
            s_dt = to_date_or_none(ex.get("start_date"))
            e_dt = to_date_or_none(ex.get("end_date"))
            
            cur.execute(insert_sql, (
                ex.get("title"),
                ex.get("description"),
                ex.get("address"),
                ex.get("author"),
                s_dt, e_dt,
                to_time_or_none(ex.get("open_time")),
                to_time_or_none(ex.get("close_time")),
                ex.get("img_url", []),
                ex.get("gallery_name"),
                today
            ))
            saved_count += 1
            
        conn.commit()
        print(f"[DB] 총 {saved_count}개 저장 완료")

    except Exception as e:
        print(f"[DB Error] {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# ==============================
# 실행부
# ==============================
if __name__ == "__main__":
    data = crawl_seoulnoin_single_page()
    
    output_path = "seoulNoin.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    if data:
        print(f"\n[완료] JSON 저장됨. DB 저장을 시도합니다.")
        save_to_postgres(data)
    else:
        print("\n[종료] 저장할 데이터가 없습니다.")