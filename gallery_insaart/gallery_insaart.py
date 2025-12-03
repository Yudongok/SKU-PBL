from urllib.parse import urljoin
# Playwright의 동기(Sync)버전 API를 쓰기 위한 것
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime

# 갤러리 인사아트의 현재 전시 url
LIST_URL = "https://galleryinsaart.com/exhibitions-current/"


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
      'AM 10:00 ~ PM 19:00'
      '10:00 ~ 18:00'
      '10:00-18:00'
      '10:00 – 18:00(월요일 휴관)'
    -> ('AM 10:00', 'PM 19:00') 또는 ('10:00', '18:00')
    """
    if not operating_hour:
        return "", ""

    # 괄호 뒤 설명 제거
    base = operating_hour.split("(", 1)[0].strip()
    # ~, -, – 기준으로 쪼개기
    parts = re.split(r"\s*[-~–]\s*", base)
    if len(parts) != 2:
        # 쪼개기 실패하면 전체를 open_time으로만 사용
        return base, ""

    open_time = parts[0].strip()
    close_time = parts[1].strip()
    return open_time, close_time


# ==============================
# 크롤러 본체
# ==============================

def crawl_exhibitions():
    with sync_playwright() as p:                    # sync_playwright(): 동기(Sync)방식으로 Playwright를 실행
        browser = p.chromium.launch(headless=True)  # headless=True: chromium 브라우저 창을 띄우지 않고 백그라운드에서 실행됨(속도 빠름)
        page = browser.new_page()                   # 새 탭을 하나 엶

        # 1) 현재전시 페이지 접속
        page.goto(LIST_URL, timeout=60_000)         # 페이지 접속
        page.wait_for_timeout(3000)                 # 3초 정도 기다려서 로딩 여유

        exhibitions = []                            # 전시 정보를 담을 리스트
        detail_urls = []

        # ▶ 전시 제목(h4 안의 a) 기준으로 목록 수집
        h4_links = page.locator("h4 a")             # 전시 제목에 해당하는 링크들이 h4 안에 <a>로 들어있다고 가정하고 그것들만 모음
        count = h4_links.count()                    # 전시 개수(제대로 쿼리 됐는지 확인용 출력)
        print(f"[리스트] 전시 개수(h4 a): {count}")

        for i in range(count):
            link = h4_links.nth(i)
            title_kr = link.inner_text().strip()           # 전시 제목 (예: 노진숙 개인전)
            href = link.get_attribute("href") or ""        # <a href="...">의 링크
            detail_url = urljoin(LIST_URL, href)           # 목록 페이지 기준으로 상세 페이지의 절대 URL 조인해서 만들기

            # 이 전시가 속한 전시장(h3)을 바로 위에서 찾기
            h4 = link.locator("xpath=ancestor::h4[1]")                                  # a태그를 감싸고 있는 바로 위의 h4요소
            section_loc = h4.locator("xpath=preceding::h3[1]")                          # h4 위쪽에 있는 가장 가까운 h3 하나 -> h3가 전시장 이름 역할
            section = section_loc.inner_text().strip() if section_loc.count() else ""   # 전시장 이름

            # h4 아래 p[2]를 기간으로 사용
            date_loc = h4.locator("xpath=following-sibling::p[2]")      # 전시 기간
            date_text = date_loc.inner_text().strip() if date_loc.count() else ""

            # 운영 시간 텍스트
            operating_hour = "AM 10:00 ~ PM 19:00"

            # 날짜/시간 파싱
            start_date, end_date = parse_operating_day(date_text)
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "address": section,             # 본 전시장 (1F) 등
                    "title": title_kr,              # 전시 제목
                    "operatingDay": date_text,      # 기간 (raw)
                    "operatingHour": operating_hour,  # 운영시간 (raw)
                    "start_date": start_date,       # 파싱된 시작일
                    "end_date": end_date,           # 파싱된 종료일
                    "open_time": open_time,         # 파싱된 오픈 시간
                    "close_time": close_time,       # 파싱된 마감 시간
                    "galleryName": "갤러리인사아트"
                }
            )

            detail_urls.append(detail_url)

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) exhibitions에 쌓아둔 각 전시별 detail_url로 들어가서 상세 페이지를 열고 3초동안 대기
        for i, ex in enumerate(exhibitions):
            url = detail_urls[i]
            print(f"\n[상세] 이동: {ex['title']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (2) 작가 정보 (h5가 여러 개 있는 형태라 가정)
            artist = ""

            h5s = page.locator("h5, h6")   # 작가 정보가 <h5>, <h6> 태그에 있을 것으로 가정하고 locator를 설정해서 모두 찾음
            h5_count = h5s.count()
            if h5_count >= 1:              # 찾은 요소 개수가 1개 이상일 경우, 첫 번째 요소의 텍스트를 작가로 간주하여 추출
                artist = h5s.nth(0).inner_text().strip()

            # (3) 설명 텍스트: div.fusion-text.fusion-text-2 안의 텍스트만 크롤링
            description = ""

            text_container = page.locator("div.fusion-text.fusion-text-2")              # 특정 CSS클래스를 가진 div 요소를 먼저 찾음
            if text_container.count():                                                  # div요소를 찾았다면, 컨테이너 내부의 모든 <p> 태그 텍스트를 수집해서 줄 바꿈 문자로 연결
                paragraphs = text_container.locator("p").all_inner_texts()
                description = "\n".join([p.strip() for p in paragraphs if p.strip()])
            else:
                # 혹시 해당 div가 없을 때를 대비한 fallback, 페이지 전체의 모든 <p> 태그 텍스트를 수집하여 설명으로 사용
                paragraphs = page.locator("p").all_inner_texts()
                description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # (4) 이미지 URL들: wp-content/uploads/2025/ 이 포함된 것만 크롤링
            img_elements = page.locator("img")                              # 페이지 내의 모든 <img> 태그를 찾음
            img_count = img_elements.count()
            image_urls = []
            for idx in range(img_count):
                src = img_elements.nth(idx).get_attribute("src") or ""
                src = src.strip()
                if not src:
                    continue

                # 지정하신 경로가 포함된 이미지들만 수집
                if "wp-content/uploads/2025/" not in src:        # 추출된 이미지 url에 특정 경로가 포함되지 않으면 images_url에 담지 않음
                    continue

                image_urls.append(src)

            # 수집된 정보를 exhibition dict 에 상세정보 추가
            ex["artist"] = artist
            ex["description"] = description
            ex["imageUrl"] = image_urls

            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


if __name__ == "__main__":
    data = crawl_exhibitions()

    # 1) Json파일로 저장
    output_path = "gallery_insaart.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n JSON 저장 완료: {output_path}")
        print(f"전시 개수: {len(data)}")

    print("=========json저장 완료=========")
