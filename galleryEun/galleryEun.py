import re
import json
import time
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

# 1. 갤러리은 리스트 페이지 (전시 목록이 있는 게시판/메인)
# 만약 메인 페이지에 슬라이더가 있다면 "https://galleryeun.com/index.php" 사용
LIST_URL = "https://galleryeun.com/index.php?module=Board&action=SiteBoard&sMode=SELECT_FORM&iBrdNo=1"

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
            if not link_el.count(): continue
            
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

            exhibitions.append({
                "title": title_kr,
                "subtitle": subtitle,
                "operatingDay": operating_day,
                "detailUrl": detail_url,
                "galleryName": "갤러리은",
                "operatingHour": "AM 10:30 ~ PM 18:30(연중무휴)",
                "imageUrl": [thumb_url] if thumb_url else [],
                # 상세 페이지에서 채울 값들 초기화
                "address": "",
                "description": "", # 작가노트/서문
                "artistProfile": "", # 작가 프로필
                "artist": "" # 작가 이름 (추정)
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
                    pass # 텍스트가 없는 경우도 있을 수 있음
            except Exception as e:
                print(f"  -> [오류] 상세 페이지 접속 실패: {e}")
                continue

            # ---------------------------------------------------------
            # (1) 이미지 수집 (style 속성의 url 추출)
            # ---------------------------------------------------------
            detail_images = ex["imageUrl"][:] # 썸네일 포함
            
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
            # (2) 텍스트 수집 및 분리 (서문 vs 프로필)
            # ---------------------------------------------------------
            text_container = page.locator(".t_st2").first
            
            description = ""
            profile = ""
            artist_name = ""

            if text_container.count():
                full_text = text_container.inner_text()
                
                # 작가 이름 추정 (보통 첫 줄에 "OOO 개인전" 형태로 있음)
                lines = [line.strip() for line in full_text.splitlines() if line.strip()]
                if lines:
                    first_line = lines[0]
                    # "김철수 개인전" -> "김철수" 추출 시도
                    artist_name = first_line.replace("개인전", "").replace("초대전", "").strip()

                # "[ Profile ]" 혹은 "[프로필]" 기준으로 텍스트 분리
                split_keyword = "[ Profile ]"
                if split_keyword not in full_text:
                    split_keyword = "[프로필]"

                if split_keyword in full_text:
                    parts = full_text.split(split_keyword)
                    description = parts[0].strip() # 앞부분: 작가노트/서문
                    profile = split_keyword + "\n" + parts[1].strip() # 뒷부분: 프로필
                else:
                    description = full_text.strip() # 구분이 없으면 통째로 설명
            
            ex["description"] = description
            ex["artistProfile"] = profile
            ex["artist"] = artist_name
            
            # 주소 추출 (footer address)
            footer = page.locator("address").first
            if footer.count():
                ex["address"] = footer.inner_text().strip()
            else:
                ex["address"] = "서울 종로구 인사동길 45-1" # 기본값

        browser.close()

        # 저장 전 URL 정리
        for ex in exhibitions:
            if "detailUrl" in ex: del ex["detailUrl"]

        return exhibitions

if __name__ == "__main__":
    data = crawl_exhibitions()

    if data:
        output_path = "galleryEun.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n========= 저장 완료 =========")
        print(f"파일 위치: {output_path}")
        print(f"총 데이터 개수: {len(data)}")
        
        # 확인용 출력 (첫 번째 데이터)
        print("\n[첫 번째 데이터 샘플]")
        print(f"제목: {data[0]['title']}")
        print(f"작가: {data[0]['artist']}")
        print(f"이미지 수: {len(data[0]['imageUrl'])}")
        print(f"설명 일부: {data[0]['description'][:50]}...")
    else:
        print("수집된 데이터가 없습니다.")