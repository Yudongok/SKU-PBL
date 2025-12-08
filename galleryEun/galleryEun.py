import re
import json
import time
from urllib.parse import urljoin
from datetime import datetime, date
from playwright.sync_api import sync_playwright
import os
import psycopg2  # PostgreSQL 연동용

# 1. 갤러리은 리스트 페이지 (전시 목록이 있는 게시판/메인)
# 만약 메인 페이지에 슬라이더가 있다면 "https://galleryeun.com/index.php" 사용
LIST_URL = "https://galleryeun.com/index.php?module=Board&action=SiteBoard&sMode=SELECT_FORM&iBrdNo=1"


# ==============================
# 날짜/시간 파싱 유틸 함수들
# ==============================

def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.12.3', '2025. 12. 03', '12.8', '8' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    if not part:
        return None

    # 앞뒤 공백 제거
    s = part.strip()
    # "2025. 12. 03" -> "2025.12.03" (점 주변 공백 제거)
    s = re.sub(r"\s*\.\s*", ".", s)

    # 1) YYYY.MM.DD 형식
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mth, d = map(int, m.groups())
        try:
            return datetime(year=y, month=mth, day=d)
        except ValueError:
            return None

    # 2) MM.DD 형식 (연도는 base_date에서 가져오기)
    if base_date:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

    # 3) DD 형식 (연/월은 base_date에서 가져오기)
    if base_date:
        m = re.match(r"^(\d{1,2})$", s)
        if m:
            d = int(m.group(1))
            try:
                return datetime(year=base_date.year, month=base_date.month, day=d)
            except ValueError:
                return None

    # 다 안 맞으면 실패
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

    # ~, -, –(엔대시) 기준으로 앞/뒤 나누기 (한 번만 split)
    parts = re.split(r"\s*[-~–]\s*", text, maxsplit=1)
    if len(parts) != 2:
        # 형식 이상하면 그냥 통째로 start_date에 넣고 end_date는 빈 값
        return text, ""

    start_part, end_part = parts[0], parts[1]

    # 앞 날짜 먼저 파싱
    start_dt = parse_single_date(start_part)
    if not start_dt:
        # 시작 날짜도 못 읽으면 그냥 통째로 start_date
        return text, ""

    # 뒤 날짜는 연/월이 없으면 앞 날짜 기준으로 보완
    end_dt = parse_single_date(end_part, base_date=start_dt)
    if not end_dt:
        # 끝 날짜 못 읽으면 시작만 반환
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(operating_hour: str):
    """
    예시:
      'AM 10:30 ~ PM 18:30(연중무휴)'
      '10:00 ~ 18:00'
      '10:00-18:00'
      '10:00 – 18:00(월요일 휴관)'
    -> ('10:30', '18:30') 또는 ('10:00', '18:00')  # HH:MM 형태로 반환
    """
    if not operating_hour:
        return "", ""

    # 괄호 뒤 설명 제거
    base = operating_hour.split("(", 1)[0].strip()
    # 문자열에서 HH:MM 패턴만 추출
    times = re.findall(r"\d{1,2}:\d{2}", base)

    if len(times) >= 2:
        return times[0], times[1]
    elif len(times) == 1:
        return times[0], ""
    else:
        return "", ""


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


# ==============================
# 크롤러 본체
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # =================================================================
        # [1단계] 리스트 페이지 크롤링 (슬라이더 수집)
        # =================================================================
        print(">>> [1단계] 리스트 페이지 접속 중...")
        try:
            page.goto(LIST_URL, timeout=60_000)
            page.wait_for_load_state("domcontentloaded")
            
            # 슬라이더가 로딩될 때까지 대기
            try:
                page.wait_for_selector(".slick-list .slick-slide", timeout=5000)
            except:
                print("⚠️ 슬라이더를 찾을 수 없습니다. URL을 확인해주세요.")
                browser.close()
                return []

        except Exception as e:
            print(f"[오류] 페이지 접속 실패: {e}")
            browser.close()
            return []

        exhibitions = []
        
        # 가짜(cloned) 슬라이드 제외하고 진짜만 선택
        slides = page.locator(".slick-list .slick-slide:not(.slick-cloned)")
        count = slides.count()
        print(f"[리스트] 발견된 원본 전시 개수: {count}")

        for i in range(count):
            slide = slides.nth(i)
            
            # (1) 링크
            link_el = slide.locator("a").first
            if not link_el.count():
                continue
            
            href = link_el.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (2) 텍스트 (제목/날짜)
            title_el = slide.locator("p.st1 span").first
            raw_title = title_el.inner_text() if title_el.count() else ""
            title_kr = " ".join(raw_title.split())

            st2_texts = slide.locator("p.st2").all_inner_texts()
            st2_texts = [t.strip() for t in st2_texts if t.strip()]

            subtitle = ""
            operating_day = ""
            if len(st2_texts) >= 2:
                subtitle = st2_texts[0]
                operating_day = st2_texts[-1]
            elif len(st2_texts) == 1:
                if any(char.isdigit() for char in st2_texts[0]):
                    operating_day = st2_texts[0]
                else:
                    subtitle = st2_texts[0]

            # (3) 썸네일 (style 속성 파싱)
            thumb_url = ""
            img_dummy = slide.locator(".img_dummy").first
            if img_dummy.count():
                style_attr = img_dummy.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style_attr)
                if m:
                    src = m.group(1).strip()
                    thumb_url = urljoin(LIST_URL, src)

            # (4) 운영 시간 / 날짜 파싱
            operating_hour = "AM 10:30 ~ PM 18:30(연중무휴)"
            start_date, end_date = parse_operating_day(operating_day)
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append({
                "title": title_kr,
                "subtitle": subtitle,
                "operatingDay": operating_day,           # raw (지금은 안 씀)
                "start_date": start_date,               # 파싱 후
                "end_date": end_date,
                "detailUrl": detail_url,
                "galleryName": "갤러리은",
                "operatingHour": operating_hour,        # raw (지금은 안 씀)
                "open_time": open_time,                 # 'HH:MM'
                "close_time": close_time,
                "imageUrl": [thumb_url] if thumb_url else [],
                # 상세 페이지에서 채울 값들
                "address": "",
                "description": "",      # 작가노트/서문
                "artistProfile": "",    # 작가 프로필
                "artist": ""            # 작가 이름/리스트
            })

        print(f"[리스트] 총 {len(exhibitions)}개 리스트 확보 완료.\n")

        # =================================================================
        # [2단계] 상세 페이지 크롤링
        # =================================================================
        for idx, ex in enumerate(exhibitions):
            url = ex["detailUrl"]
            print(f"[{idx + 1}/{len(exhibitions)}] 상세 이동: {ex['title']}")

            try:
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("domcontentloaded")
                # 텍스트(.t_st2)가 뜰 때까지 기다림
                try:
                    page.wait_for_selector(".t_st2", timeout=3000)
                except:
                    pass  # 텍스트가 없는 경우도 있을 수 있음
            except Exception as e:
                print(f"  -> [오류] 상세 페이지 접속 실패: {e}")
                continue

            # ---------------------------------------------------------
            # (1) 이미지 수집 (style 속성의 url 추출)
            # ---------------------------------------------------------
            detail_images = ex["imageUrl"][:]  # 썸네일 포함
            
            # A. 상단 대표 이미지 (.ex_li .img_dummy)
            hero_imgs = page.locator(".ex_li .img_dummy").all()
            for el in hero_imgs:
                style = el.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style)
                if m:
                    full_url = urljoin(url, m.group(1).strip())
                    detail_images.append(full_url)

            # B. 하단 갤러리 이미지들 (.container .gal_dummy)
            gallery_imgs = page.locator(".container .gal_dummy").all()
            for el in gallery_imgs:
                style = el.get_attribute("style") or ""
                m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style)
                if m:
                    full_url = urljoin(url, m.group(1).strip())
                    detail_images.append(full_url)

            # 중복 제거
            ex["imageUrl"] = list(dict.fromkeys(detail_images))
            print(f"  -> 이미지: {len(ex['imageUrl'])}개 수집")

            # ---------------------------------------------------------
            # (2) 작가 이름 후보: 작품 리스트 <a class="gallery"> 블록
            # ---------------------------------------------------------
            artist_list: list[str] = []

            def add_artist_name(name: str):
                name = name.strip()
                if name and name not in artist_list:
                    artist_list.append(name)

            gallery_blocks = page.locator("a.gallery")
            gb_count = gallery_blocks.count()
            for i_g in range(gb_count):
                a_g = gallery_blocks.nth(i_g)
                title_attr = (a_g.get_attribute("title") or "").strip()
                if title_attr:
                    # "김명주, 책가도-사유의 꽃, ..." -> "김명주"
                    first_part = title_attr.split(",")[0].strip()
                    add_artist_name(first_part)

                # a.gallery 다음에 오는 <a><p>텍스트도 참고
                p_el = a_g.locator("xpath=./following-sibling::a[1]/p")
                if p_el.count():
                    p_text = p_el.inner_text().strip()
                    first_part = p_text.split(",")[0].strip()
                    add_artist_name(first_part)

            # ---------------------------------------------------------
            # (3) 텍스트 수집 및 분리 (서문 vs 프로필, 참여 작가)
            # ---------------------------------------------------------
            text_container = page.locator(".t_st2").first
            
            description = ""
            profile = ""
            artist_section_text = ""

            if text_container.count():
                full_text = text_container.inner_text()

                # 3-1) [ Profile ] / [프로필] 기준 프로필 분리
                split_keyword = None
                if "[ Profile ]" in full_text:
                    split_keyword = "[ Profile ]"
                elif "[프로필]" in full_text:
                    split_keyword = "[프로필]"

                if split_keyword:
                    parts = full_text.split(split_keyword, 1)
                    before_profile = parts[0]
                    after_profile = parts[1]
                    description_base = before_profile.strip()
                    profile = split_keyword + "\n" + after_profile.strip()
                else:
                    description_base = full_text.strip()

                # 3-2) '참여 작가' 블록 분리
                if "참여 작가" in description_base:
                    before_artist, after_artist = description_base.split("참여 작가", 1)
                    description = before_artist.strip()
                    artist_section_text = after_artist
                else:
                    description = description_base

                # 3-3) '참여 작가'에서 이름들 파싱
                if "참여 작가" in full_text and not artist_section_text:
                    # 혹시 위에서 못 잘랐으면 full_text 기준으로 다시
                    _, artist_section_text = full_text.split("참여 작가", 1)

                if artist_section_text:
                    lines_after = [ln.strip() for ln in artist_section_text.splitlines() if ln.strip()]
                    if lines_after:
                        # 여러 줄에 나뉘어 있을 수도 있으니 전부 이어서 쉼표 기준으로 분리
                        joined = " ".join(lines_after)
                        for cand in re.split(r"[、,]", joined):
                            add_artist_name(cand)

                # 3-4) fallback: 첫 줄이 "OOO 개인전" / "OOO 초대전" 형태일 수 있음
                lines = [line.strip() for line in full_text.splitlines() if line.strip()]
                if lines:
                    first_line = lines[0]
                    fallback_name = first_line.replace("개인전", "").replace("초대전", "").strip()
                    # 이미 artist_list에 있으면 add_artist_name에서 중복 체크됨
                    add_artist_name(fallback_name)

            ex["description"] = description
            ex["artistProfile"] = profile
            ex["artist"] = ", ".join(artist_list)

            # 주소 추출 (footer address)
            footer = page.locator("address").first
            if footer.count():
                ex["address"] = footer.inner_text().strip()
            else:
                ex["address"] = "서울 종로구 인사동길 45-1"  # 기본값

        browser.close()

        # 저장 전 URL 정리
        for ex in exhibitions:
            if "detailUrl" in ex:
                del ex["detailUrl"]

        return exhibitions


# ==============================
# DB 저장 함수
# ==============================

def save_to_postgres(exhibitions):
    """
    exhibition 테이블 구조 (다른 크롤러와 동일 가정):

      id           BIGINT PK
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
    db_host = os.getenv("POSTGRES_HOST", "3.34.46.99")  # 필요 시 변경
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

            # end_date NOT NULL → 없으면 스킵
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
                    ex.get("artist") or "",           # author = artist 문자열
                    start_dt,
                    end_dt,
                    open_t,
                    close_t,
                    0,                                # views 기본값 0
                    ex.get("imageUrl", []),           # 배열 컬럼
                    ex.get("galleryName"),
                    None,                             # phone_num
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

    if data:
        # 1) JSON 저장 (백업/디버깅용)
        output_path = "galleryEun.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n========= JSON 저장 완료 =========")
        print(f"파일 위치: {output_path}")
        print(f"총 데이터 개수: {len(data)}")
        
        # 2) DB 저장
        save_to_postgres(data)

        # 3) 확인용 출력 (첫 번째 데이터)
        print("\n[첫 번째 데이터 샘플]")
        print(f"제목: {data[0].get('title')}")
        print(f"작가: {data[0].get('artist')}")
        print(f"start_date: {data[0].get('start_date')}, end_date: {data[0].get('end_date')}")
        print(f"open_time: {data[0].get('open_time')}, close_time: {data[0].get('close_time')}")
        print(f"이미지 수: {len(data[0].get('imageUrl', []))}")
        print(f"설명 일부: {data[0].get('description', '')[:50]}...")
    else:
        print("수집된 데이터가 없습니다.")
