from urllib.parse import urljoin
# Playwright의 동기(Sync)버전 API를 쓰기 위한 것
from playwright.sync_api import sync_playwright
import json

# 갤러리 인사아트의 현재 전시 url
LIST_URL = "https://galleryinsaart.com/exhibitions-current/"

def crawl_exhibitions():
    with sync_playwright() as p:                    # sync_playwright(): 동기(Sync)방식으로 Playwright를 실행
        browser = p.chromium.launch(headless=True)  # headless=True: chromium 브라우저 창을 띄우지 않고 백그라운드에서 실행됨(속도 빠름)
        page = browser.new_page()                   # 새 탭을 하나 엶

        # 1) 현재전시 페이지 접속
        page.goto(LIST_URL, timeout=60_000)         # 페이지 접속
        page.wait_for_timeout(3000)                 # 3초 정도 기다려서 로딩 여유

        exhibitions = []                            # 전시 정보를 담을 리스트

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
            # locator()는 웹 페이지 내의 특정 요소(Element)를 찾기 위한 지정자(Pointer)를 만드느 함수이다. 단순히 페이지 전체에서 찾는 것이 아니라, 이미 찾은 링크를 기준으로 상대적인 위치를 찾는 역할을 하고있다.
            h4 = link.locator("xpath=ancestor::h4[1]")                                  # a태그를 감싸고 있는 바로 위의 h4요소
            section_loc = h4.locator("xpath=preceding::h3[1]")                          # h4 위쪽에 있는 가장 가까운 h3 하나 -> h3가 전시장 이름 역할
            section = section_loc.inner_text().strip() if section_loc.count() else ""   # if 조건이 참이면 즉, section_loc.count()가 있으면 '.inner_text()'로 태그 안에 있는 텍스트를 가져옴 '.strip()'으로 텍스트 앞뒤의 불필요한 공백이나 줄바꿈 제거해서 section에 넣음

            # h4 아래 p[1], p[2], p[3]를 부제 / 기간 / 장소로 사용
            subtitle_loc = h4.locator("xpath=following-sibling::p[1]")      # 전시 부제
            date_loc     = h4.locator("xpath=following-sibling::p[2]")      # 전시 기간

            subtitle = subtitle_loc.inner_text().strip() if subtitle_loc.count() else ""
            date_text = date_loc.inner_text().strip() if date_loc.count() else ""

            exhibitions.append(
                {
                    "section": section,        # 본 전시장 (1F) 등
                    "title_kr": title_kr,      # 전시 제목
                    "subtitle": subtitle,      # 부제
                    "date": date_text,         # 기간
                    "detail_url": detail_url,  # 상세페이지 링크
                }
            )

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) exhibitions에 쌓아둔 각 전시별 detail_url로 들어가서 상세 페이지를 열고 3초동안 대기
        for ex in exhibitions:
            url = ex["detail_url"]
            print(f"\n[상세] 이동: {ex['title_kr']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            # (2) 작가 정보 (h5가 여러 개 있는 형태라 가정)
            artist = ""

            h5s = page.locator("h5, h6")   # 작가 정보가 <h5>, <h6> 태그에 있을 것으로 가정하고 locator를 설정해서 모두 찾음
            h5_count = h5s.count()
            if h5_count >= 1:              # 찾은 요수 개수가 1개 이상일 경우, 첫 번째 요소의 텍스트를 작가로 간주하여 추출
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
                if "https://galleryinsaart.com/wp-content/uploads/2025/" not in src:        # 추출된 이미지 url에 특정 경로가 포함되지 않으면 images_url에 담지 않음
                    continue

                image_urls.append(src)

            # 수집된 정보를 exhibition dict 에 상세정보 추가
            ex["artist"] = artist
            ex["description"] = description
            ex["images"] = image_urls

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

    for ex in data:
        print("\n==================== 전시 ====================")
        print("전시장(섹션):", ex["section"])
        print("리스트 제목:", ex["title_kr"])
        print("부제:", ex["subtitle"])
        print("기간(리스트):", ex["date"])
        print("작가:", ex.get("artist", ""))

        print("\n[설명 텍스트]")
        desc = ex.get("description", "")
        print(desc[:500], "..." if len(desc) > 500 else "")

        print("\n[이미지 URL들]")
        for img in ex.get("images", []):
            print(" -", img)
