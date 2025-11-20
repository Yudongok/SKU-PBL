from urllib.parse import urljoin
# Playwright의 동기(Sync)버전 API를 쓰기 위한 것
from playwright.sync_api import sync_playwright
import json

# 갤러리 인사아트의 현재 전시 url
LIST_URL = "https://www.insa1010.com/28"

def crawl_exhibitions():
    with sync_playwright() as p:                    # sync_playwright(): 동기(Sync)방식으로 Playwright를 실행
        browser = p.chromium.launch(headless=True)  # headless=True: chromium 브라우저 창을 띄우지 않고 백그라운드에서 실행됨(속도 빠름)
        page = browser.new_page()                   # 새 탭을 하나 엶

        # 1) 현재전시 페이지 접속
        page.goto(LIST_URL, timeout=60_000)         # 페이지 접속
        page.wait_for_timeout(3000)                 # 3초 정도 기다려서 로딩 여유

        exhibitions = []                            # 전시 정보를 담을 리스트

        cards = page.locator(
            "div.list-style.hide_writer.hide_time.hide_hit_cnt.hide_comment_cnt.grid_01.type_grid.overlay_text.hover_show_overlay.container_border"
        )

        card_count = cards.count()
        print("카드 개수:", card_count)

        for i in range(card_count):
            card = cards.nth(i)
            link = card.locator("a").first
            
            if link.count() == 0:
                print(f"[경고] {i}번 카드에서 링크를 찾지 못했습니다.")
                continue

            href = link.get_attribute("href") or ""

            detail_url = urljoin(LIST_URL, href)           # 목록 페이지 기준으로 상세 페이지의 절대 URL 조인해서 만들기

            print(f"[리스트] {i}번 카드 상세 URL: {detail_url}")
            exhibitions.append(
                {
                    "detail_url": detail_url
                }
            )

        print(f"[리스트] 수집된 전시 수: {len(exhibitions)}")

        # 2) exhibitions에 쌓아둔 각 전시별 detail_url로 들어가서 상세 페이지를 열고 3초동안 대기
        for ex in exhibitions:
            url = ex["detail_url"]
            print(f"\n[상세] 이동: {ex['detail_url']} -> {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3000)

            title = ""
            place = ""
            preiod = ""

            h1s = page.locator("h1")
            h1_count = h1s.count()
            if h1_count >= 2:
                title = h1s.nth(0).inner_text().strip()
            if h1_count >= 3:
                preiod = h1s.nth(0).inner_text().strip()
            if h1_count >= 4:
                palce = h1s.nth(0).inner_text().strip()
            

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
                if "https://cdn.imweb.me/upload/" not in src:        # 추출된 이미지 url에 특정 경로가 포함되지 않으면 images_url에 담지 않음
                    continue

                image_urls.append(src)

            # 수집된 정보를 exhibition dict 에 상세정보 추가
            ex["title"] = title
            ex["preiod"] = preiod
            ex["plce"] = place
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
        print("작가:", ex.get("artist", ""))
        print("제목:", ex.get("title", ""))
        print("기간:", ex.get("preiod", ""))
        print("장소:", ex.get("place", ""))

        print("\n[설명 텍스트]")
        desc = ex.get("description", "")
        print(desc[:500], "..." if len(desc) > 500 else "")

        print("\n[이미지 URL들]")
        for img in ex.get("images", []):
            print(" -", img)
