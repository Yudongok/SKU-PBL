import time
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime

# 인사아트센터 현재 전시 URL
LIST_URL = "https://maruartcenter.co.kr/default/exhibit/exhibit01.php?sub=01"


# part변수에 "2025.12.3"이나 "12.8" 같은 날짜 조각 하나가 들어옴.
# base_date에는 연도/월이 생략된 날짜 조각을 해석할 때 기준으로 쓰는 날짜임.(옵션이라 None일수도 있고, datetime일 수도 있음)
def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    """
    part: '2025.12.3', '12.8', '8' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜
    """
    # 문자열 양쪽 공백을 제거함
    s = part.strip()

    # 1) YYYY.MM.DD 형식인지 확인
    m = re.match(r"^\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*$", s)
    # 위의 형식이 맞을경우 y=2025, mth=12, d=3같은 숫자로 변환
    if m:
        y, mth, d = map(int, m.groups())
        try:
            # datetime객체를 만들어서 반환
            return datetime(year=y, month=mth, day=d)
        # 만약 날짜가 말이 안되면 None 반환
        except ValueError:
            return None

    # 2) MM.DD 형식 (연도는 base_date에서 가져오기, base_date가 있을때만 시도)
    if base_date:
        m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s*$", s)
        if m:
            mth, d = map(int, m.groups())
            try:
                # base_date에서 연도만 빼와서 넣어줌
                return datetime(year=base_date.year, month=mth, day=d)
            except ValueError:
                return None

    # 3) DD 형식 (연/월은 base_date에서 가져오기)
    if base_date:
        m = re.match(r"^\s*(\d{1,2})\s*$", s)
        if m:
            d = int(m.group(1))
            try:
                # base_date에서 연도와 월만 빼와서 넣어줌
                return datetime(year=base_date.year, month=base_date.month, day=d)
            except ValueError:
                return None

    # 다 안 맞으면 실패
    return None


# operating_day변수에 "2025.12.3-23.8"이나 "2025.12.3 ~ 12.8"처럼 "시작~끝" 형식의 문자열이 들어옴
def parse_operating_day(operating_day: str):
    """
    예시:
      '2025.12.3-12.8'      -> ('2025-12-03', '2025-12-08')
      '2025.12.3~12.8'      -> ('2025-12-03', '2025-12-08')
      '2025.12.3 ~ 2025.12.8' -> ('2025-12-03', '2025-12-08')
    실패 시: (원본 문자열, "")
    """
    # None, 빈 문자열이 들어오면 빈 문자열 리턴
    if not operating_day:
        return "", ""

    # 양쪽 공백 제거
    text = operating_day.strip()

    # ~ 또는 - 기준으로 split -> 두 조각으로 잘림
    parts = re.split(r"\s*[~-]\s*", text)
    # 만약 두 조각으로 안 잘렸을 경우
    if len(parts) != 2:
        # 형식 이상하면 그대로 반환
        return text, ""

    # 앞 뒤 조각을 변수에 담음
    start_part, end_part = parts[0], parts[1]

    # 앞 날짜 먼저 파싱
    start_dt = parse_single_date(start_part)
    if not start_dt:
        # 시작 날짜도 못 읽으면 그냥 통째로 start_date로
        return text, ""

    # 뒤 날짜는 연/월이 없으면 앞 날짜 기준으로 보완
    end_dt = parse_single_date(end_part, base_date=start_dt)
    if not end_dt:
        # 끝 날짜 못 읽으면 시작만 반환
        return start_dt.strftime("%Y-%m-%d"), ""

    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def parse_operating_hour(operating_hour: str):
    """
    예시: 'AM 10:30 ~ PM 18:30(연중무휴)'
    -> ('AM 10:30', 'PM 18:30')
    """
    if not operating_hour:
        return "", ""

    # 괄호 뒤 설명 제거
    base = operating_hour.split("(", 1)[0].strip()
    parts = [p.strip() for p in base.split("~")]
    if len(parts) != 2:
        return base, ""

    open_time = parts[0]
    close_time = parts[1]
    return open_time, close_time


def crawl_exhibitions():
    # Playwright 시작&끝날 때 자동 정리(브라우저 프로세스 깨끗이 종료)
    with sync_playwright() as p:
        # 크롬 계열 브라우저를 "headless(창 안 띄움)"로 실행
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()   # 탭 하나 열어줌

        # ----------------------------------------------------------------
        # 1. 리스트 페이지 수집
        # ----------------------------------------------------------------
        print(">>> [1단계] 리스트 페이지 접속 중...")
        page.goto(LIST_URL, timeout=60_000) # 전시 리스트 페이지로 이동(최대 60초 기다림)
        page.wait_for_timeout(3000)         # 3초 그냥 기다리기(스크립트, 이미지 로딩 등 안정화용)

        exhibitions = []

        # 제목(span.gallery_title)을 기준으로 잡습니다.
        title_spans = page.locator("span.gallery_title")
        count = title_spans.count()
        print(f"[리스트] 발견된 전시 개수: {count}")

        # 각 전시에 대해 반복
        for i in range(count):
            title_span = title_spans.nth(i)     # i번쨰 전시 제목 span

            # (1) 제목 추출
            raw_title = title_span.inner_text()     # 태그 안의 텍스트(줄바꿈, 탭 등 포함)
            title_kr = " ".join(raw_title.split())  # 공백/줄바꿈을 전부 쪼갠 뒤 하나의 공백으로 다시 합치기

            # (2) 상세페이지 URL 추출
            link_el = title_span.locator("xpath=./ancestor::a[1]")  # 이 span을 감싸고 있는 가장 가까운 a태그를 찾음
            href = link_el.get_attribute("href") or ""              # href가져와서 LIST_URL과 urljoin
            detail_url = urljoin(LIST_URL, href)

            # (3) 날짜 (operatingDay -> start_date, end_date 분리)
            title_row = title_span.locator("xpath=./ancestor::tr[1]")           # title span에 들어있는 tr을 찾음
            date_row = title_row.locator("xpath=./following-sibling::tr[1]")    # 바로 다음 줄 tr을 찾고 data_row만 봄(following-sibiling::tr[1])

            operating_day = ""
            if date_row.count():
                raw_date = date_row.inner_text().strip()    # 날짜가 있으면 inner_text()로 가져옴
                operating_day = (
                    raw_date.replace("[", "")
                    .replace("]", "")
                    .replace("기간 :", "")
                    .strip()
                )

            start_date, end_date = parse_operating_day(operating_day)

            # (4) 운영 시간 (operatingHour -> open_time, close_time 분리)
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
                    "detailUrl": detail_url,  # 일단은 저장 (이동용)
                    "address": "",
                    "description": "",
                    "imageUrl": [],
                }
            )

        print(f"[리스트] 총 {len(exhibitions)}개 수집 완료. 상세 페이지 크롤링 시작...\n")

        # ----------------------------------------------------------------
        # 2. 상세 페이지 순회
        # ----------------------------------------------------------------
        for i, ex in enumerate(exhibitions):
            url = ex["detailUrl"]  # 여기서 사용
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
            imgs = page.locator("#post_area img").all()     # (#post_area)안의 모든 <img>를 찾음
            if not imgs:
                imgs = page.locator("div[style*='text-align: center'] img").all()   # 없을 경우 style에 text-align: center가 들어간 div안의 <img>를 fallback으로 사용

            for img in imgs:
                src = img.get_attribute("src")      # <img>의 src속성을 읽음
                if src and "u_image" in src:        # u_image가 들어간것만 필터
                    full_url = urljoin(url, src)    # urljoin
                    image_urls.append(full_url)     # image_urls에 img url넣음

            ex["imageUrl"] = list(dict.fromkeys(image_urls))    # ex["imageUrl"]에 저장
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
                clean = text.strip()    # 각 텍스트 블록 앞 뒤 공백 제거
                if not clean:           # 빈 문자열이면 패스
                    continue

                # 위치 찾기
                if ("마루아트센터" in clean or "관" in clean) and not is_note:  # "마루아트센터"나 "관"이 들어가있을 경우
                    if len(clean) < 50:                                      # 글자 길이가 50을 넘지 않을경우
                        location_text = clean                                # 주소/전시장 정보로 판단, ex) "마루아트센터 신관 3층 2관"

                # 작가노트/작품설명 시작 지점
                if "[작가노트]" in clean or "[작품설명]" in clean or "[작품 설명]" in clean:    # 텍스트에 이런 키워드가 들어가는 순간
                    is_note = True                                                          # 작가 노트/설명으로 취급
                    continue  # 타이틀 자체는 제외

                if is_note:
                    desc_lines.append(clean)

            ex["address"] = location_text
            ex["description"] = "\n".join(desc_lines)

        browser.close()

        # ----------------------------------------------------------------
        # detailUrl는 최종 JSON에는 제외
        # ----------------------------------------------------------------
        for ex in exhibitions:
            if "detailUrl" in ex:
                del ex["detailUrl"]

        return exhibitions


if __name__ == "__main__":
    data = crawl_exhibitions()

    if data is not None:
        output_path = "maruArtCenter.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("\n========= 저장 완료 =========")
        print(f"파일 위치: {output_path}")
        print(f"총 데이터 개수: {len(data)}")
    else:
        print("데이터 수집 실패")
