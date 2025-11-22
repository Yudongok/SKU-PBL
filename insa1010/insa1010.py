from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from openai import OpenAI
from dotenv import load_dotenv
import json
import os

# ------------------------
# 기본 설정
# ------------------------

# .env 파일에서 환경변수 로드
load_dotenv()

# 갤러리 인사아트의 현재 전시 url
LIST_URL = "https://www.insa1010.com/28"

# OpenAI 클라이언트 (환경변수 OPENAI_API_KEY 사용)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ------------------------
# GPT로 필드 추출하는 함수
# ------------------------

def extract_fields_with_gpt(description_text: str, image_urls: list[str]) -> dict:
    """
    설명 텍스트와 이미지 URL 리스트를 GPT에 보내서
    title, description, imageUrl, operatingHour, operatingDay 를 JSON 형식으로 반환받는다.
    """
    system_prompt = """
당신은 전시 정보 정리 도우미입니다.
입력으로 전시 소개 텍스트와 이미지 URL 목록이 주어집니다.
이 정보를 보고 아래 형식의 JSON만 순수 텍스트로 출력하세요.

{
  "title": "...",
  "description": "...",
  "imageUrl": "...",
  "operatingHour": "...",
  "operatingDay": "..."
}

규칙:
- title: 전시 제목으로 자연스럽게 한 줄.
- description: 소개/설명 텍스트. 한국어로 자연스럽게.
- imageUrl: 주어진 imageUrls 중에서 가장 대표 이미지 1개를 선택해서 그대로 넣기. 없다면 빈 문자열 "".
- operatingHour: 관람 가능 시간 (예: "10:00 ~ 18:00").
- operatingDay: 전시 기간이나 요일 정보 (예: "2025.01.01 ~ 2025.01.07", "월요일 휴관" 등 텍스트로 자연스럽게).
- 반드시 유효한 JSON만 출력하고, 설명 문장이나 다른 텍스트는 출력하지 마세요.
"""

    user_content = {
        "description": description_text,
        "imageUrls": image_urls,
    }

    response = client.chat.completions.create(
        model="gpt-4o-mini",  # 필요에 따라 다른 모델명으로 변경 가능
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_content, ensure_ascii=False)
            },
        ],
        temperature=0.2,
    )

    raw = response.choices[0].message.content.strip()

    # GPT가 돌려준 내용을 JSON으로 파싱
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 혹시 JSON 포맷이 깨질 수 있으니 최소 방어 로직
        print("⚠ GPT 응답 JSON 파싱 실패. 원문:")
        print(raw)
        data = {
            "title": "",
            "description": description_text,
            "imageUrl": image_urls[0] if image_urls else "",
            "operatingHour": "",
            "operatingDay": "",
        }

    # key가 없을 수도 있으니 기본값 채우기
    data.setdefault("title", "")
    data.setdefault("description", description_text)
    data.setdefault("imageUrl", image_urls[0] if image_urls else "")
    data.setdefault("operatingHour", "")
    data.setdefault("operatingDay", "")

    return data


# ------------------------
# 크롤러 함수
# ------------------------

def crawl_exhibitions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) 리스트 페이지 접속
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(3000)

        exhibitions = []   # 최종 JSON에 들어갈 dict 리스트
        detail_urls = []   # 상세 페이지 이동용 URL 리스트
        seen_urls = set()

        # 게시글 상세로 가는 링크 수집
        links = page.locator("a[href*='bmode=view']")
        link_count = links.count()
        print("전시 상세 링크 개수:", link_count)

        for i in range(link_count):
            href = links.nth(i).get_attribute("href") or ""
            if not href:
                continue

            detail_url = urljoin(LIST_URL, href.split("#")[0])

            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            print(f"[리스트] {i}번 상세 URL: {detail_url}")
            detail_urls.append(detail_url)
            exhibitions.append({})  # 내용은 나중에 채움

        print(f"[리스트] 수집된 전시 수(중복 제거): {len(exhibitions)}")

        # 2) 상세 페이지 크롤링
        for ex, url in zip(exhibitions, detail_urls):
            print(f"\n[상세] 이동: {url}")
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(2000)

            # (A) p 태그 텍스트 모으기
            text_container = page.locator("div.fusion-text.fusion-text-2")
            if text_container.count() > 0:
                paragraphs = text_container.locator("p").all_inner_texts()
            else:
                paragraphs = page.locator("p").all_inner_texts()

            # 설명 텍스트(원본)를 하나의 문자열로 합치기
            cleaned_paragraphs = [
                t.strip(" ﻿\u200b") for t in paragraphs if t.strip(" ﻿\u200b")
            ]
            description_text = "\n".join(cleaned_paragraphs)

            # (B) 이미지 URL 수집
            img_elements = page.locator("img")
            img_count = img_elements.count()
            image_urls = []

            for idx in range(img_count):
                src = img_elements.nth(idx).get_attribute("src") or ""
                src = src.strip()
                if not src:
                    continue

                # 필요에 따라 필터링 조건 수정 가능
                if "https://cdn.imweb.me/upload/" not in src:
                    continue

                image_urls.append(src)

            # (C) GPT에게 정보 추출 요청
            gpt_data = extract_fields_with_gpt(description_text, image_urls)

            # (D) exhibition dict 구성 (detail_url은 저장하지 않음)
            ex.update({
                "title": gpt_data["title"],
                "description": gpt_data["description"],
                "imageUrl": gpt_data["imageUrl"],
                "operatingHour": gpt_data["operatingHour"],
                "operatingDay": gpt_data["operatingDay"],
                "images": image_urls,  # 전체 이미지 리스트도 같이 저장
            })

            print(f"[상세] 제목: {ex['title']}")
            print(f"[상세] 운영시간: {ex['operatingHour']}")
            print(f"[상세] 운영일: {ex['operatingDay']}")
            print(f"[상세] 대표 이미지: {ex['imageUrl']}")
            print(f"[상세] 이미지 개수: {len(image_urls)}")

        browser.close()
        print(f"\n[최종] 전시 {len(exhibitions)}개 상세 정보 수집 완료")
        return exhibitions


# ------------------------
# 메인 실행부
# ------------------------

if __name__ == "__main__":
    # 크롤링 실행
    data = crawl_exhibitions()

    # Json 파일로 저장
    output_path = "gallery_insaart_gpt.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON 저장 완료: {output_path}")
    print(f"전시 개수: {len(data)}")

    # 콘솔에 요약 출력
    for ex in data:
        print("\n==================== 전시 ====================")
        print("제목:", ex.get("title", ""))
        print("운영시간:", ex.get("operatingHour", ""))
        print("운영일:", ex.get("operatingDay", ""))
        print("대표 이미지:", ex.get("imageUrl", ""))

        print("\n[설명 텍스트]")
        desc = ex.get("description", "")
        print(desc[:500], "..." if len(desc) > 500 else "")
