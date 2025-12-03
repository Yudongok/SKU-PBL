from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time

BASE_URL = "http://tongingallery.com/exhibitions"

# ==============================
# 유틸 함수
# ==============================
def parse_single_date(part: str, base_date: datetime | None = None) -> datetime | None:
    if not part: return None
    s = re.sub(r"\s*\.\s*", ".", part.strip())
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m: return datetime(*map(int, m.groups()))
    if base_date:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
        if m: return datetime(base_date.year, int(m.group(1)), int(m.group(2)))
        m = re.match(r"^(\d{1,2})$", s)
        if m: return datetime(base_date.year, base_date.month, int(m.group(1)))
    return None

def parse_operating_day(operating_day: str):
    if not operating_day: return "", ""
    parts = re.split(r"\s*[-~–]\s*", operating_day.strip(), maxsplit=1)
    if len(parts) != 2: return operating_day, ""
    start_dt = parse_single_date(parts[0])
    if not start_dt: return operating_day, ""
    end_dt = parse_single_date(parts[1], base_date=start_dt)
    return start_dt.strftime("%Y-%m-%d"), (end_dt.strftime("%Y-%m-%d") if end_dt else "")

def parse_operating_hour(operating_hour: str):
    if not operating_hour: return "", ""
    base = operating_hour.split("(", 1)[0].strip()
    parts = re.split(r"\s*[-~–]\s*", base)
    if len(parts) != 2: return base, ""
    return parts[0].strip(), parts[1].strip()

# ==============================
# 크롤러 본체
# ==============================
def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"접속 중: {BASE_URL}")
        try:
            page.goto(BASE_URL, timeout=60_000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"접속 실패: {e}")
            return []

        exhibitions = []

        # -----------------------------
        # 1. 목록 수집
        # -----------------------------
        onview_header = page.locator("h6", has_text="ON VIEW").first
        if onview_header.count():
            header_row = onview_header.locator("xpath=ancestor::div[contains(@class,'doz_row')][1]")
            image_row = header_row.locator("xpath=following-sibling::div[contains(@class,'doz_row')][2]")
            text_row = header_row.locator("xpath=following-sibling::div[contains(@class,'doz_row')][3]")
            
            cnt = text_row.locator("div.text-table").count()
            links = image_row.locator("a._fade_link")
            thumbs = image_row.locator("img.org_image")
            
            print(f"[ON VIEW] {cnt}개 발견")

            for i in range(cnt):
                container = text_row.locator("div.text-table").nth(i)
                p_tags = container.locator("p")
                if p_tags.count() < 3: continue

                title = p_tags.nth(0).inner_text().strip()
                date_text = p_tags.nth(1).inner_text().strip()
                section = p_tags.nth(2).inner_text().strip()
                href = links.nth(i).get_attribute("href") if links.count() > i else ""
                detail_url = urljoin(BASE_URL, href) if href else ""
                src = thumbs.nth(i).get_attribute("src") if thumbs.count() > i else ""
                thumb_url = urljoin(BASE_URL, src) if src else ""
                start_date, end_date = parse_operating_day(date_text)
                
                exhibitions.append({
                    "title": title,
                    "address": section,
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": "AM 10:30",
                    "close_time": "PM 18:30",
                    "galleryName": "통인화랑",
                    "detailUrl": detail_url,
                    "artist": "",
                    "description": "",
                    "imageUrl": [thumb_url] if thumb_url else [],
                })

        upcoming_header = page.locator("h6", has_text="UPCOMING").first
        if upcoming_header.count():
            inside = upcoming_header.locator("xpath=ancestor::div[contains(@class,'inside')][1]")
            up_cnt = inside.locator("div.text-table").count()
            up_links = inside.locator("a._fade_link")
            up_thumbs = inside.locator("img.org_image")
            
            for i in range(up_cnt):
                div = inside.locator("div.text-table").nth(i)
                raw = div.inner_text()
                if "202" not in raw: continue
                p_tags = div.locator("p")
                if p_tags.count() < 3: continue
                
                title = p_tags.nth(0).inner_text().strip()
                date_text = p_tags.nth(1).inner_text().strip()
                section = p_tags.nth(2).inner_text().strip()
                href = up_links.first.get_attribute("href") if up_links.count() else ""
                detail_url = urljoin(BASE_URL, href) if href else ""
                src = up_thumbs.first.get_attribute("src") if up_thumbs.count() else ""
                thumb_url = urljoin(BASE_URL, src) if src else ""
                start_date, end_date = parse_operating_day(date_text)
                
                exhibitions.append({
                    "title": title,
                    "address": section,
                    "start_date": start_date,
                    "end_date": end_date,
                    "open_time": "AM 10:300",
                    "close_time": "PM 18:30",
                    "galleryName": "통인화랑",
                    "detailUrl": detail_url,
                    "artist": "",
                    "description": "",
                    "imageUrl": [thumb_url] if thumb_url else [],
                })

        print(f"\n[리스트 완료] 총 {len(exhibitions)}개 수집됨.\n")

        # -----------------------------
        # 2. 상세 페이지 순회
        # -----------------------------
        for ex in exhibitions:
            url = ex['detailUrl']
            if not url: continue
            
            print(f"👉 이동: {ex['title']} -> {url}")
            try:
                page.goto(url, timeout=60000)
                page.keyboard.press("End") 
                page.wait_for_load_state("networkidle")
                time.sleep(1)
            except Exception as e:
                print(f"   ❌ 로딩 에러: {e}")
                continue

            ex["artist"] = ""

            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            # 1. 태그 삭제 (스크립트 등)
            for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
                element.decompose()

            # 2. 전체 텍스트 추출 (구분자 사용)
            all_text = soup.get_text(separator=" |LINE| ")
            lines = all_text.split(" |LINE| ")
            
            valid_lines = []
            
            # ----------------------------------------------------------------
            # [필터링 키워드] 이 단어들이 포함되면 '쓰레기 줄'로 간주하고 버립니다.
            # 디버깅 로그에 나온 0~3번 줄의 특징을 모두 넣었습니다.
            # ----------------------------------------------------------------
            garbage_keywords = [
                "통인화랑", "tong-in",       # Index 0 제거용
                "게시물", "댓글", "답글",    # Index 1 제거용
                "공지", "알려줍니다",        # Index 2 제거용
                "로그인", "login",           # Index 3 제거용
                "all right reserved", "copyright", "insadong" # 푸터 제거용
            ]

            for line in lines:
                clean_line = line.strip()
                if not clean_line: continue
                
                lower_line = clean_line.lower()

                # A. 쓰레기 키워드 검사
                is_garbage = False
                for kw in garbage_keywords:
                    if kw in lower_line:
                        is_garbage = True
                        break
                
                if is_garbage:
                    continue # 쓰레기면 건너뜀 (저장 안 함)

                # B. 길이 검사 (너무 짧은 잡문 제거)
                if len(clean_line) < 15: continue

                # C. 한글 검사 (한글이 있어야 진짜 설명)
                if re.search(r"[가-힣]", clean_line):
                    valid_lines.append(clean_line)

            # 합치기
            description = "\n".join(valid_lines).strip()
            ex["description"] = description

            if description:
                print(f"   ✅ 최종 설명: {description[:30].replace('\n', ' ')}...")
            else:
                print("   ⚠️ 설명을 찾지 못했습니다.")

            # 이미지 추출
            gallery_images = page.evaluate("""() => {
                const imgs = [];
                const targets = document.querySelectorAll('div._gallery_wrap ._img_wrap, div.img_wrap._img_wrap');
                targets.forEach(el => {
                    let src = el.getAttribute('data-src') || el.getAttribute('data-bg');
                    if (src) {
                        src = src.replace(/^url\\(['"]?/, '').replace(/['"]?\\)$/, '');
                        imgs.push(src);
                    }
                });
                return imgs;
            }""")

            current_images = ex.get("imageUrl", [])[:]
            for raw_src in gallery_images:
                if not raw_src: continue
                full_url = raw_src.strip()
                if not full_url.startswith("http"):
                    full_url = urljoin(BASE_URL, full_url)
                current_images.append(full_url)
            
            ex["imageUrl"] = list(dict.fromkeys(current_images))

        for ex in exhibitions:
            ex.pop("detailUrl", None)

        browser.close()
        return exhibitions

if __name__ == "__main__":
    data = crawl_exhibitions()
    with open("tongInGallery.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\n🎉 저장 완료: tongInGallery.json")