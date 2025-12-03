from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json
import re
from datetime import datetime

# 인사아트센터 현재 전시 URL
LIST_URL = "https://www.insaartcenter.com/bbs/board.php?bo_table=exhibition_current"


def parse_single_date(part, base_date=None):
    """
    part: '2025. 11. 26', '2025.12.3', '12.8', '8' 같은 문자열
    base_date: 연/월이 생략된 경우 참고할 기준 날짜 (datetime 또는 None)
    """
    if not part:
        return None

    # 앞뒤 공백 제거
    s = part.strip()
    
    # "2025. 11. 26" -> "2025.11.26" 처럼 점 주변 공백 제거
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

    # ~, -, –(엔대시) 기준으로 앞/뒤 나누기 ("maxsplit=1"은 한 번만 split)
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
    예시: 'AM 10:00 ~ PM 19:00(화요일 정기 휴무)'
    -> ('AM 10:00', 'PM 19:00')
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 현재 전시 리스트 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        detail_urls = []

        # ▶ 각 전시 블록: div.gall_text_href
        items = page.locator("div.gall_text_href")
        count = items.count()
        print(f"[리스트] 전시 개수(div.gall_text_href): {count}")

        for i in range(count):
            item = items.nth(i)

            # (1) 제목 + 상세 페이지 URL
            link = item.locator("a.bo_tit")
            if not link.count():
                continue

            raw_title = link.inner_text()
            # 여러 공백/줄바꿈 정리
            title_kr = " ".join(raw_title.split())

            href = link.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (2) 기간 / 전시장 / 갤러리명
            rows = item.locator(".list-spec table tr")
            row_count = rows.count()

            operating_day = rows.nth(0).inner_text().strip() if row_count > 0 else ""
            hall = rows.nth(1).inner_text().strip() if row_count > 1 else ""
            gallery_txt = rows.nth(2).inner_text().strip() if row_count > 2 else ""

            # operating_day → start_date / end_date
            start_date, end_date = parse_operating_day(operating_day)

            # 운영시간 고정값 → open_time / close_time 분리
            operating_hour = "AM 10:00 ~ PM 19:00(화요일 정기 휴무)"
            open_time, close_time = parse_operating_hour(operating_hour)

            exhibitions.append(
                {
                    "title": title_kr,                    # 전시 제목
                    "start_date": start_date,             # 시작일
                    "end_date": end_date,                 # 종료일
                    "address": hall,                      # 전시장 정보
                    "galleryName": gallery_txt or "인사아트센터",
                    "open_time": open_time,
                    "close_time": close_time,
                    "artist": "",
                    "description": "",
                    "imageUrl": [],
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

            # (1) 작가 정보
            artist = ""
            spec = page.locator("div.spec")
            if spec.count():
                rows = spec.locator("table tr")
                row_count = rows.count()

                for j in range(row_count):
                    th = rows.nth(j).locator("th")
                    if not th.count():
                        continue

                    label = th.inner_text().strip()
                    if label == "작가":
                        td = rows.nth(j).locator("td")
                        if td.count():
                            artist = "".join(td.inner_text().split())
                        break

            # (2) 설명 텍스트
            content = page.locator("#bo_v_con")
            if not content.count():
                content = page.locator(".bo_v_con")

            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = p_loc.all_inner_texts()
                else:
                    paragraphs = [content.inner_text()]
            else:
                paragraphs = page.locator("p").all_inner_texts()

            description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # (3) 이미지 URL
            image_urls = []

            gallery_items = page.locator("#img-gallery li")
            item_count = gallery_items.count()

            for idx in range(item_count):
                li = gallery_items.nth(idx)

                # 1순위: 원본 이미지 data-src
                src = li.get_attribute("data-src")

                # data-src가 없으면, <img>의 src 사용
                if not src:
                    img_el = li.locator("img")
                    if img_el.count():
                        src = img_el.nth(0).get_attribute("src")

                if not src:
                    continue

                src = src.strip()
                if not src:
                    continue

                # 인사아트센터 전시 이미지 폴더만 필터링
                if "/data/file/exhibition_current/" not in src:
                    continue

                image_urls.append(src)

            # 중복 제거
            image_urls = list(dict.fromkeys(image_urls))

            # 상세 정보 exhibition dict에 반영
            ex["artist"] = artist
            ex["description"] = description
            ex["imageUrl"] = image_urls

            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


if __name__ == "__main__":
    data = crawl_exhibitions()

    # JSON 파일로 저장
    output_path = "insaArt.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")
    print("=========json저장 완료=========")
