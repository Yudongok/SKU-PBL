from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json

# 인사아트센터 현재 전시 URL
LIST_URL = "https://maruartcenter.co.kr/default/exhibit/exhibit01.php?sub=01"


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

            exhibitions.append(
                {
                    "title": title_kr,                # 전시 제목
                    "operatingDay": operating_day,    # 기간
                    "address": hall,                  # 전시장 정보 (예: B1F 제1전시장)
                    "galleryName": gallery_txt or "인사아트센터",
                    "operatingHour": "AM 10:00 ~ PM 19:00(화요일 정기 휴무)",
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

            # (1) 작가 정보 (페이지 구조에 따라 수정 필요)
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

            # (2) 설명 텍스트: 본문(#bo_v_con 또는 .bo_v_con)을 우선 사용
            description = ""

            content = page.locator("#bo_v_con")
            if not content.count():
                content = page.locator(".bo_v_con")

            # p태그 있는지 여부 파악
            if content.count():
                p_loc = content.locator("p")
                if p_loc.count():
                    paragraphs = p_loc.all_inner_texts()
                else:
                    paragraphs = [content.inner_text()]

            # p태그도 없고 #bo_v_con, .bo_v_con도 없으면 fallback으로 페이지 전체 <p>사용
            else:
                paragraphs = page.locator("p").all_inner_texts()

            # paragraphs 리스트를 돌면서 앞에 있는 p.strip()으로 공백을 제거한다. if p.strip()은 공백만 있는 문자열은 버리고 앞뒤 공백을 제거한 문자열만 담는다는 뜻이다.
            # 즉 if p.strip()은 내용이 있는지 확인하는 조건이다. 만약 없다면 False가 나와서 description에 저장하지 않고 넘어가게 된다.
            # if p.strip()은 그냥 조건 체크용이기 때문에 실제로 p.strip()이 실행되어 p값을 변경하지 않는다. 따라서 마지막에 p.strip()해주는 것이다.
            description = "\n".join([p.strip() for p in paragraphs if p.strip()])

            # (3) 이미지 URL

            image_urls = []

            # 슬라이더 안의 li (clone 제외하고 싶으면 :not(.clone) 사용)
            gallery_items = page.locator("#img-gallery li")
            item_count = gallery_items.count()

            for idx in range(item_count):
                li = gallery_items.nth(idx)

                # 1순위: 원본 이미지 data-src
                src = li.get_attribute("data-src")

                # 혹시 data-src가 없다면, <img>의 src를 fallback으로 사용
                if not src:
                    img_el = li.locator("img")
                    if img_el.count():
                        src = img_el.nth(0).get_attribute("src")

                if not src:
                    continue

                src = src.strip()
                if not src:
                    continue

                # 인사아트센터 전시 이미지 폴더만 필터링하고 싶을 때
                if "/data/file/exhibition_current/" not in src:
                    continue

                image_urls.append(src)

            # 중복 제거 (clone li 등에서 같은 이미지가 반복될 수 있으므로)
            image_urls = list(dict.fromkeys(image_urls))  # 순서 유지하면서 중복 제거

            ex["imageUrl"] = image_urls
            print(f"[상세] 이미지 개수: {len(image_urls)}")


            # 상세 정보 exhibition dict에 추가
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
