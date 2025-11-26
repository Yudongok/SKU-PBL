import time
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import json

# 인사아트센터 현재 전시 URL
LIST_URL = "https://maruartcenter.co.kr/default/exhibit/exhibit01.php?sub=01"

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ----------------------------------------------------------------
        # 1. 리스트 페이지 수집
        # ----------------------------------------------------------------
        print(">>> [1단계] 리스트 페이지 접속 중...")
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []
        
        # 제목(span.gallery_title)을 기준으로 잡습니다.
        title_spans = page.locator("span.gallery_title")
        count = title_spans.count()
        print(f"[리스트] 발견된 전시 개수: {count}")

        for i in range(count):
            title_span = title_spans.nth(i)
            
            # (1) 제목
            raw_title = title_span.inner_text()
            title_kr = " ".join(raw_title.split())

            # (2) 링크
            link_el = title_span.locator("xpath=./ancestor::a[1]")
            href = link_el.get_attribute("href") or ""
            detail_url = urljoin(LIST_URL, href)

            # (3) 날짜
            title_row = title_span.locator("xpath=./ancestor::tr[1]")
            date_row = title_row.locator("xpath=./following-sibling::tr[1]")
            
            operating_day = ""
            if date_row.count():
                raw_date = date_row.inner_text().strip()
                operating_day = raw_date.replace("[", "").replace("]", "").replace("기간 :", "").strip()

            exhibitions.append({
                "title": title_kr,
                "operatingDay": operating_day,
                "detailUrl": detail_url, # 일단은 저장 (이동용)
                "galleryName": "마루아트센터",
                "operatingHour": "AM 10:30 ~ PM 18:30(연중무휴)",
                "address": "",
                "description": "",
                "imageUrl": []
            })

        print(f"[리스트] 총 {len(exhibitions)}개 수집 완료. 상세 페이지 크롤링 시작...\n")

        # ----------------------------------------------------------------
        # 2. 상세 페이지 순회
        # ----------------------------------------------------------------
        for i, ex in enumerate(exhibitions):
            url = ex['detailUrl'] # 여기서 사용
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
            imgs = page.locator("#post_area img").all()
            if not imgs:
                 imgs = page.locator("div[style*='text-align: center'] img").all()

            for img in imgs:
                src = img.get_attribute("src")
                if src and "u_image" in src:
                    full_url = urljoin(url, src)
                    image_urls.append(full_url)
            
            ex['imageUrl'] = list(dict.fromkeys(image_urls))
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
                clean = text.strip()
                if not clean: continue

                # 위치 찾기
                if ("마루아트센터" in clean or "관" in clean) and not is_note:
                    if len(clean) < 50:
                        location_text = clean

                # [버그 수정] 작가노트 조건문 수정
                # 기존 코드: if "A" or "B" in text: (X) -> 항상 True가 됨
                # 수정 코드: if "A" in text or "B" in text: (O)
                if "[작가노트]" in clean or "[작품설명]" in clean or "[작품 설명]" in clean:
                    is_note = True
                    continue # 타이틀 자체는 제외
                
                if is_note:
                    desc_lines.append(clean)

            ex['address'] = location_text
            ex['description'] = "\n".join(desc_lines)
            
        browser.close()
        
        # ----------------------------------------------------------------
        # [NEW] 최종 정리: detailUrl 삭제
        # ----------------------------------------------------------------
        for ex in exhibitions:
            if 'detailUrl' in ex:
                del ex['detailUrl']

        return exhibitions

if __name__ == "__main__":
    data = crawl_exhibitions()

    if data is not None:
        output_path = "maruArtCenter.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n========= 저장 완료 =========")
        print(f"파일 위치: {output_path}")
        print(f"총 데이터 개수: {len(data)}")
    else:
        print("데이터 수집 실패")