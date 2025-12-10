# SKU-PBL
Course repository for the Fall 2025 Project-Based Learning (PBL) class.

# 🎨 Insadong Gallery Crawler (인사동 갤러리 전시 정보 통합 수집기)

인사동 주요 갤러리 웹사이트에서 **현재 전시중(Current Exhibition)**인 정보를 수집하여 프로젝트 데이터베이스(PostgreSQL)에 적재하는 크롤링 모듈입니다.

정적 페이지는 `BeautifulSoup`으로 가볍게 처리하고, 동적 렌더링이 필요한 사이트는 `Playwright`를 사용하여 하이브리드 방식으로 수집합니다. 특히, **비정형 텍스트 데이터(작품 설명, 작가 정보 등)는 GPT API를 활용하여 정밀하게 구조화**하였습니다.

## 🛠 Tech Stack
<img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=Python&logoColor=white"/> <img src="https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=Playwright&logoColor=white"/> <img src="https://img.shields.io/badge/BeautifulSoup-000000?style=flat-square&logo=BeautifulSoup&logoColor=white"/> <img src="https://img.shields.io/badge/OpenAI_API-412991?style=flat-square&logo=OpenAI&logoColor=white"/> <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=PostgreSQL&logoColor=white"/>

## ✨ Key Features

### 1. Hybrid Crawling Strategy
- **Static Sites**: `BeautifulSoup4`를 사용하여 속도 최적화
- **Dynamic Sites**: `Playwright`를 사용하여 JavaScript 렌더링 및 인터랙션(클릭, 스크롤) 처리

### 2. AI-Powered Data Parsing (GPT API)
- 갤러리마다 제각각인 전시 설명 텍스트(`<p>` 태그 뭉치)를 GPT API에 전송하여 **정형 데이터로 변환**
- **추출 필드**: `전시 소개(Description)`, `참여 작가(Artists)`, `전시장 위치/층수(Location)` 등을 자동으로 분리 및 분류

### 3. PostgreSQL Database Integration
- 수집된 데이터를 관계형 데이터베이스(PostgreSQL) 스키마에 맞춰 자동 적재 및 중복 방지 처리

## 🏛 Crawler Status (현재 작업 현황)
현재 총 **6곳**의 주요 갤러리에 대한 크롤러 구현 및 데이터 적재 테스트를 완료했습니다.

- [x] **갤러리 은 (Gallery Eun)**
- [x] **갤러리 인사아트 (Gallery Insa Art)**
- [x] **인사 1010 (Insa 1010)**
- [x] **인사아트 (Insa Art)**
- [x] **마루아트센터 (Maru Art Center)**
- [x] **통인 갤러리 (Tongin Gallery)**

## 🚀 Installation & Setup

### 1. Prerequisites
이 프로젝트는 Python 3.x 환경에서 실행됩니다.
```bash
# 필수 패키지 설치
pip install -r requirements.txt

# Playwright 브라우저 바이너리 설치 (필수)
playwright install