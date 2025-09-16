# BOM_Project



<img src="file:///C:/Users/kimda/Downloads/제목을%20입력해주세요..png" title="" alt="제목을 입력해주세요..png" data-align="center">



본 프로젝트는 제품 성분표(BOM)가 포함된 문서를 업로드하면,  **AI 에이전트 팀**이 각 성분의 대한민국 식품안전법령 준수 여부를 자동으로 검증하고 종합 보고서를 생성하는 시스템입니다.

복잡하고 반복적인 법규 검토 작업을 자동화하여 제품 개발 및 품질 관리의 효율성을 극대화하는 것을 목표로 합니다.

---

## ✨ 주요 기능

- **📄 파일 기반 성분 추출**: Excel, PDF 등 다양한 형식의 제품 성분표 파일을 업로드하면 LLM이 처리하기 용이한 형태로 전처리를 수행합니다.
  
  **🤖 자율 AI 에이전트 워크플로우**:

- - **TaskDistributor**: 추출된 성분 목록을 분석하고 여러 처리 에이전트에게 작업을 병렬로 분배합니다.
  
  - **Processors (B1, B2, B3)**: 할당된 성분들을 MongoDB 데이터베이스에서 검색하여 관련 법규 정보를 조회합니다.
  
  - **Comparators (C1, C2, C3)**: 조회된 법규와 제품의 사용 목적을 비교하여 각 성분의 적합성(적합, 조건부 적합, 부적합)을 분석합니다.
  
  - **FinalCollector**: 모든 분석 결과를 취합하여 명확하고 실행 가능한 최종 보고서를 작성합니다.

- **🔍 실시간 모니터링 및 디버깅**: WebSocket을 통해 각 AI 에이전트의 작업 현황을 웹 UI에서 실시간으로 확인할 수 있습니다. 
  
  > 현재 세션별 상세 로그를 다운로드할 수 있는 디버깅 기능을 추가하였습니다

- **🚀 비동기 처리**: FastAPI와 Motor를 사용하여 여러 요청을 효율적으로 동시에 처리합니다.

---

## ⚙️ 시스템 아키텍처

본 시스템은 명확한 역할 분담을 가진 다중 에이전트(Multi-Agent)가 유기적으로 협력하는 `GraphFlow` 구조로 설계되었습니다.

<img src="file:///C:/Users/kimda/Downloads/Untitled.png" title="" alt="Untitled.png" data-align="center">

---

## 🛠️ 기술 스택

- **Backend**: FastAPI, Uvicorn, Python 3.9+

- **AI Framework**: Microsoft AutoGen

- **Database**: MongoDB (with Motor async driver)

- **Real-time Communication**: WebSockets

- **File Processing**: MarkItDown

- **Configuration**: python-dotenv

---

## 🚀 설치 및 실행 방법

#### 1. 소스 코드 복제 (Clone)

```
git clone https://github.com/hs-1971423-kimjihun/BOM_project.git
cd BOM_project
```

#### 2. 가상 환경 생성 및 활성화

```
# 가상 환경 생성
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

#### 3. 필요 라이브러리 설치

프로젝트에 필요한 모든 패키지를 `requirements.txt` 파일을 통해 설치합니다.

```
pip install -r requirements.txt
```

#### 4. 환경 변수 설정

프로젝트 루트 디렉터리에 `.env` 파일을 생성하고 아래 내용을 채워주세요.

```
# .env.example

# OpenAI API Key
OPENAI_API_KEY="sk-..."

# MongoDB Configuration
MONGO_URL="mongodb://your_mongo_ip:27017"
MONGO_DATABASE="food"
MONGO_COLLECTION="test"
```

#### 5. MongoDB 준비

- 실행 환경에 MongoDB가 설치 및 실행되어 있어야 합니다.

- `MONGO_DATABASE`와 `MONGO_COLLECTION`에 해당하는 데이터베이스와 컬렉션에 식품안전법령 데이터를 미리 입력해두어야 합니다. (코드는 샘플 데이터를 자동으로 삽입합니다.)

#### 6. 애플리케이션 실행

```
python main.py
```

서버가 성공적으로 실행되면 터미널에 다음과 같은 메시지가 나타납니다.

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

---

## 💻 사용 방법

1. 웹 브라우저에서 `http://localhost:8000` 주소로 접속합니다.

2. **'파일 업로드'** 섹션에서 성분 목록이 포함된 Excel 파일을 선택합니다.

3. **'파일 업로드'** 버튼을 클릭하여 서버로 파일을 전송하고 Markdown으로 변환된 내용을 확인합니다.

4. **'검증 시작'** 버튼을 클릭하여 AI 에이전트 워크플로우를 시작합니다.

5. **'에이전트 작업 현황'** 및 **'디버깅 정보'** 패널을 통해 실시간 처리 과정을 모니터링합니다.

6. 작업이 완료되면 화면 하단에 최종 검증 보고서가 표시됩니다.
