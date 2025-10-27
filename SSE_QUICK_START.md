# SSE 빠른 시작 가이드

## 🚀 5분 안에 SSE 실행하기

---

## 1️⃣ 백엔드 패키지 설치

```bash
cd backend
pip install sse-starlette
```

또는 전체 패키지 재설치:

```bash
pip install -r requirements.txt
```

---

## 2️⃣ 서버 실행

### 백엔드 (터미널 1)
```bash
cd backend
python main.py
```

**확인 메시지**:
```
INFO:     FDA ReAct Agent initialized successfully.
🚀 서버 준비 완료!
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 프론트엔드 (터미널 2)
```bash
cd frontend
npm start
```

**자동으로 브라우저 열림**: `http://localhost:3000`

---

## 3️⃣ 테스트

### 빠른 테스트 (5-10초)
브라우저에서 다음 질문 입력:

```
FSVP가 뭐야?
```

**예상 결과**:
```
🚀 질문을 분석하고 있습니다...
    ↓
🔍 FDA 문서를 검색하고 있습니다...
    ↓
⚖️ 검색 결과를 평가하고 있습니다...
    ↓
✍️ 답변을 생성하고 있습니다...
    ↓
✅ 답변 생성 완료

[답변이 표시됩니다]
```

### 깊이 검색 테스트 (15-25초)
```
김치를 미국에 수출하려면 어떤 절차가 필요해?
```

**예상 결과**:
```
🚀 질문을 분석하고 있습니다...
    ↓
🔍 FDA 문서를 검색하고 있습니다...
    ↓
⚖️ 검색 결과를 평가하고 있습니다...
    ↓
🧠 깊이 검색중... 정확한 답변을 찾고 있습니다
   (이 작업은 15-20초 정도 소요될 수 있습니다.)
    ↓
✅ 추가 정보 수집 완료
    ↓
✍️ 최종 답변을 생성하고 있습니다...
    ↓
✅ 답변 생성 완료

[상세한 답변이 표시됩니다]
```

---

## 4️⃣ 디버깅

### 백엔드 로그 확인
백엔드 터미널에서 다음과 같은 로그를 확인할 수 있습니다:

```
INFO:     SSE 스트리밍 요청: query='FSVP가 뭐야?', project_id=None
🔍 일반 질문 감지 - LLM 증강 적용
📚 검색할 컬렉션: ['guidance', 'ecfr', 'fsvp']
⚡ 병렬 검색 완료: 1.23초, 8개 결과
✅ 병렬 검색 결과만으로 충분 - 직접 답변 생성
```

### 프론트엔드 콘솔 확인
브라우저 개발자 도구 (F12) → Console 탭:

```javascript
SSE 연결 시작: http://localhost:8000/api/chat/stream?query=FSVP%EA%B0%80%20%EB%AD%90%EC%95%BC%3F
Status 이벤트: {status: "started", message: "질문을 분석하고 있습니다...", timestamp: 1234567890.123}
Status 이벤트: {status: "searching", message: "FDA 문서를 검색하고 있습니다...", timestamp: 1234567891.234}
Status 이벤트: {status: "evaluating", message: "검색 결과를 평가하고 있습니다...", timestamp: 1234567892.345}
Status 이벤트: {status: "generating", message: "답변을 생성하고 있습니다...", timestamp: 1234567893.456}
Result 이벤트: {content: "FSVP는...", citations: [...], keywords: [...]}
```

---

## 5️⃣ 문제 해결

### SSE 연결 실패
**증상**: 기존 로딩 박스가 표시됨 (점 3개 애니메이션)

**원인**: SSE 엔드포인트에 연결 실패

**해결 방법**:
1. 백엔드 서버가 실행 중인지 확인
2. `http://localhost:8000` 에서 서버 응답 확인
3. 콘솔에서 에러 메시지 확인
4. 폴백 메커니즘이 자동으로 작동하므로 답변은 정상적으로 표시됨

---

### 패키지 설치 오류
**증상**: `ModuleNotFoundError: No module named 'sse_starlette'`

**해결 방법**:
```bash
cd backend
pip install sse-starlette
```

또는:
```bash
pip install --upgrade sse-starlette
```

---

### CORS 에러
**증상**: 브라우저 콘솔에 CORS 에러 표시

**해결 방법**:
`backend/main.py`의 CORS 설정 확인:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 6️⃣ 성공 확인

### ✅ SSE가 정상 작동하는 경우
- [ ] 상태 메시지가 단계별로 표시됨
- [ ] 상태별 아이콘이 표시됨 (🚀, 🔍, ⚖️, ✍️ 등)
- [ ] 점 애니메이션이 표시됨
- [ ] 기존 타이머 로딩 박스가 **표시되지 않음**
- [ ] 최종 답변이 정상적으로 표시됨

### ❌ SSE가 작동하지 않는 경우 (폴백)
- [ ] 기존 타이머 로딩 박스가 표시됨
- [ ] "문서를 찾고 있어요..." 메시지 표시
- [ ] 타이머가 증가함 (⏳ X.Xs 경과)
- [ ] 최종 답변이 정상적으로 표시됨 (기존 API 사용)

---

## 📱 모바일 테스트

### 1. 컴퓨터 IP 확인
```bash
# Windows
ipconfig

# Mac/Linux
ifconfig
```

### 2. 프론트엔드 환경 변수 설정
`frontend/.env` 파일 생성:

```env
REACT_APP_API_URL=http://[컴퓨터IP]:8000
```

예시:
```env
REACT_APP_API_URL=http://192.168.0.10:8000
```

### 3. 모바일 브라우저에서 접속
```
http://[컴퓨터IP]:3000
```

예시:
```
http://192.168.0.10:3000
```

---

## 🎯 다음 단계

1. ✅ SSE 기본 동작 확인
2. 📊 다양한 질문으로 테스트
3. 🔍 백엔드 로그 분석
4. 🎨 UI/UX 개선 아이디어 제안
5. 🚀 프로덕션 배포 준비

---

## 📚 추가 문서

- **상세 구현 가이드**: `docs/backend/sse-implementation.md`
- **통합 요약**: `SSE_INTEGRATION_SUMMARY.md`
- **API 문서**: `docs/backend/api-endpoints.md`

---

## 💬 자주 묻는 질문

### Q1. SSE와 기존 API의 차이는?
**A**: SSE는 실시간 상태 업데이트를 제공합니다. 기존 API는 최종 답변만 반환합니다.

### Q2. 모든 브라우저에서 작동하나요?
**A**: 최신 브라우저(Chrome, Firefox, Safari, Edge)에서 작동합니다. IE 11 이하는 자동으로 폴백됩니다.

### Q3. SSE가 실패하면 어떻게 되나요?
**A**: 자동으로 기존 POST API로 폴백되어 답변이 정상적으로 표시됩니다.

### Q4. 여러 탭에서 동시에 사용할 수 있나요?
**A**: 네, 가능합니다. 다만 HTTP/1.1에서는 브라우저당 최대 6개의 동시 SSE 연결 제한이 있습니다.

### Q5. 프로덕션 환경에서 주의할 점은?
**A**: 
- 프록시/로드 밸런서에서 장시간 연결을 허용하도록 설정
- HTTPS 사용 권장
- 적절한 타임아웃 설정

---

## 🎉 축하합니다!

SSE 통합이 완료되었습니다. 이제 사용자는 백엔드 처리 과정을 실시간으로 확인할 수 있습니다! 🚀

