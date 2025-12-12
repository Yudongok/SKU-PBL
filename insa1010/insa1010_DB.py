from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from openai import OpenAI
from dotenv import load_dotenv
import json
import os
import re
from datetime import datetime, date
import psycopg2  # PostgreSQL 연동용

# ------------------------
# 기본 설정
# ------------------------

# .env 파일에서 환경변수 로드
load_dotenv()

# 갤러리 인사아트(인사1010)의 현재 전시 url
LIST_URL = "https://www.insa1010.com/28"

# OpenAI 클라이언트 (환경변수 OPENAI_API_KEY 사용)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ------------------------
# 공백 제거 유틸 (✅ 추가)
# ------------------------
def normalize_text(s: str) -> str:
    """None 또는 공백만 있는 텍스트를 빈 문자열로 정리"""
    if not s:
        return ""
    return s.strip()


# ------------------------
# 날짜/시간 파싱 유틸 함수들
# ------------------------

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part 예시:
      - '2025.12.3'
      - '2025-12-03'
      - '2025년 12월 3일'
      - '12.8'
      - '12월 8일'
      - '8'
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    s = part.strip()

    # 한글 날짜 표현을 점(.) 기반으로 정규화
    s = re.sub(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?", r"\1.\2.\3", s)
    s = re.sub(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일?", r"\1.\2", s)

    # -, / 를 . 로 통일
    s = s.replace("-", ".").replace("/", ".")

    # 점 주변 공백 / 중복 점 정리
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\.+", ".", s)
    s = s.strip(" .")

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
      '전시 기간: 2025. 11. 26 - 2025. 12. 15 (월요일 휴관)'
      '2025.12.3-12.8'
      '2025-12-03 ~ 2025-12-08'
      '2025년 12월 3일 ~ 12월 8일'
    -> ('YYYY-MM-DD', 'YYYY-MM-DD')
    실패 시: (원본문자열, "")
    """
    if not operating_day:
        return "", ""

    text = operating_day.strip()

    # 1) 앞쪽의 "전시 기간:", "기간:" 등 제거 (첫 숫자부터 자르기)
    m = re.search(r"\d", text)
    if not m:
        return text, ""
    text = text[m.start():]

    # 2) 괄호 안 설명 제거
    text = re.sub(r"\(.*?\)", "", text).strip()

    # 3) ~, -, – 기준으로 앞/뒤 나누기
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)

    if len(parts) != 2:
        dt = parse_single_date(text)
        if dt:
            return dt.strftime("%Y-%m-%d"), ""
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
    예시:
      '10:00 ~ 18:00'
      '10:00-18:00'
      '10:00 – 18:00(월요일 휴관)'
    -> ('10:00', '18:00')
    """
    if not operating_hour:
        return "", ""

    base = operating_hour.split("(", 1)[0].strip()
    parts = re.split(r"\s*[-~–]\s*", base)
    if len(parts) != 2:
        return base, ""

    open_time = parts[0].strip()
    close_time = parts[1].strip()
    return open_time, close_time


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


# ------------------------
# GPT로 필드 추출하는 함수
# ------------------------

def extract_fields_with_gpt(description_text: str, image_urls: list[str]) -> dict:
    system_prompt = """
당신은 전시 정보 정리 도우미입니다.
입력으로 전시 소개 텍스트와 이미지 URL 목록이 주어집니다.
이 정보를 보고 아래 형식의 JSON만 순수 텍스트로 출력하세요.

{
  "title": "...",
  "description": "...",
  "imageUrl": "...",
  "operatingHour": "...",
  "operatingDay": "...",
  "author": "..."
}

규칙:
- title: 전시 제목으로 자연스럽게 한 줄.
- description: 소개/설명 텍스트. 한국어로 자연스럽게.
- imageUrl: 주어진 imageUrls 중에서 가장 대표 이미지 1개. 없다면 "".
- operatingHour: 관람 가능 시간 (예: "10:00 ~ 18:00").
- operatingDay: 전시 기간은 반드시 'YYYY.MM.DD ~ YYYY.MM.DD' 형식.
- 필요하면 휴관 정보는 괄호로 추가 가능.
- 반드시 유효한 JSON만 출력.
- 작가를 추출해서 author에 넣고 없으면 "".
"""

    user_content = {
        "description": description_text,
        "imageUrls": image_urls,
    }

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
        temperature=0.2,
    )

    raw = response.choices[0].message.content.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("⚠ GPT 응답 JSON 파싱 실패. 원문:")
        print(raw)
        data = {
            "title": "",
            "description": description_text,
            "imageUrl": image_urls[0] if image_urls else "",
            "operatingHour": "",
            "operatingDay": "",
            "author": "",
        }

    data.setdefault("title", "")
    data.setdefault("description", description_text)
    data.setdefault("imageUrl", image_urls[0] if image_urls else "")
    data.setdefault("operatingHour", "")
    data.setdefault("operatingDay", "")
    data.setdefault("author", "")

    return data


# ------------------------
# 크롤러 함수
# ------------------------

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 리스트 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        detail_urls = []
        seen_urls = set()

        links = page.locator("a[href*='bmode=view']")
        link_count = links.count()
        print("전시 상세 링크 개수:", link_count)

        for i in range(link_count):
            href = links.nth(i).get_attribute("href") or ""
            if not href:
                continue

            detail_url = urljoin(LIST_URL, href.split("#")[0])

            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            print(f"[리스트] {i}번 상세 URL: {detail_url}")
            detail_urls.append(detail_url)
            exhibitions.append({})

        print(f"[리스트] 수집된 전시 수(중복 제거): {len(exhibitions)}")

        # 2) 상세 페이지 크롤링
        for ex, url in zip(exhibitions, detail_urls):
            print(f"\n[상세] 이동: {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(2000)

            text_container = page.locator("div.fusion-text.fusion-text-2")
            if text_container.count() > 0:
                paragraphs = text_container.locator("p").all_inner_texts()
            else:
                paragraphs = page.locator("p").all_inner_texts()

            cleaned_paragraphs = [
                t.strip(" ﻿\u200b") for t in paragraphs if t.strip(" ﻿\u200b")
            ]
            description_text = "\n".join(cleaned_paragraphs)

            img_elements = page.locator("img")
            img_count = img_elements.count()
            image_urls = []

            for idx in range(img_count):
                src = img_elements.nth(idx).get_attribute("src") or ""
                src = src.strip()
                if not src:
                    continue
                if "https://cdn.imweb.me/upload/" not in src:
                    continue
                image_urls.append(src)

            gpt_data = extract_fields_with_gpt(description_text, image_urls)

            start_date, end_date = parse_operating_day(gpt_data["operatingDay"])
            open_time, close_time = parse_operating_hour(gpt_data["operatingHour"])

            ex.update({
                "title": gpt_data["title"],
                "description": gpt_data["description"],
                "author": gpt_data["author"],
                "start_date": start_date,
                "end_date": end_date,
                "open_time": "11:00",
                "close_time": "19:00",
                "img_url": image_urls,
                "imageUrl": image_urls,
                "mainImageUrl": gpt_data["imageUrl"],
                "gallery_name": "인사1010",
                "galleryName": "인사1010",
                "address": None,
            })

            print(f"[상세] 제목: {ex['title']}")
            print(f"[상세] start_date: {ex['start_date']}, end_date: {ex['end_date']}")
            print(f"[상세] open_time: {ex['open_time']}, close_time: {ex['close_time']}")
            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ------------------------
# DB 저장 함수
# ------------------------

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

        for ex in exhibitions:
            start_dt = to_date_or_none(ex.get("start_date"))
            end_dt = to_date_or_none(ex.get("end_date"))

            if end_dt is None:
                print(f"[DB] end_date 없음, 스킵: {ex.get('title')}")
                continue

            # ✅ description이 비어있으면 스킵 (핵심 변경)
            desc = normalize_text(ex.get("description") or "")
            if not desc:
                print(f"[DB] description 없음, 스킵: {ex.get('title')}")
                continue

            open_t = to_time_or_none(ex.get("open_time"))
            close_t = to_time_or_none(ex.get("close_time"))

            cur.execute(
                insert_sql,
                (
                    ex.get("title") or "",
                    desc,  # ✅ desc 사용
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

        conn.commit()
        print(f"[DB] exhibition 테이블에 {len(exhibitions)}개 INSERT 시도 완료")

    except Exception as e:
        print("[DB] 에러 발생:", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ------------------------
# 메인 실행부
# ------------------------

if __name__ == "__main__":
    data = crawl_exhibitions()

    output_path = "insa1010_gpt.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")

    save_to_postgres(data)

    for ex in data:
        print("\n==================== 전시 ====================")
        print("제목:", ex.get("title", ""))
        print("start_date:", ex.get("start_date", ""))
        print("end_date:", ex.get("end_date", ""))
        print("open_time:", ex.get("open_time", ""))
        print("close_time:", ex.get("close_time", ""))
        print("이미지 개수:", len(ex.get("img_url", [])))
