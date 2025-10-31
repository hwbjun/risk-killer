# SSE (Server-Sent Events) 스트리밍 구현

## 📋 개요

SSE를 사용하여 백엔드 처리 과정을 프론트엔드에 실시간으로 전달하는 기능을 구현했습니다. 사용자는 검색, 평가, 깊이 검색 등 각 단계를 실시간으로 확인할 수 있습니다.

## 🔧 백엔드 구현

### 1. 새 엔드포인트

**위치**: `backend/main.py`

```python
@app.get("/api/chat/stream")
async def chat_stream(query: str, project_id: Optional[int] = None):
    """SSE를 사용한 스트리밍 채팅 엔드포인트"""
    ...
```

**특징**:
- GET 메서드 사용 (EventSource는 GET만 지원)
- 쿼리 파라미터로 질문과 프로젝트 ID 전달
- `EventSourceResponse` 사용

### 2. Agent 스트리밍 메서드

**위치**: `backend/utils/agent.py`

```python
async def chat_stream(self, query: str):
    """SSE 스트리밍 방식으로 중간 상태와 최종 답변을 생성"""
    ...
```

**이벤트 플로우**:

```
1. started → 질문 분석 시작
2. searching → FDA 문서 검색 중
3. evaluating → 검색 결과 평가 중
4. [분기점]
   Case A (충분):
     - generating → 답변 생성 중
     - result → 최종 답변
   
   Case B (불충분):
     - deep_search → 깊이 검색 중 (15~25초)
     - agent_complete → 추가 정보 수집 완료
     - generating → 최종 답변 생성 중
     - result → 최종 답변
5. completed → 완료
```

### 3. 이벤트 타입 정의

| 이벤트 타입 | 발생 시점 | 데이터 구조 |
|------------|---------|----------|
| `status` | 각 처리 단계 | `{status, message, timestamp}` |
| `result` | 최종 답변 | `{content, citations, keywords, ...}` |
| `error` | 오류 발생 | `{message, timestamp}` |

## 💻 프론트엔드 구현

### 1. EventSource 연결

**위치**: `frontend/src/App.js`

```javascript
const sendMessageSSE = (message, projectId, updatedMessages) => {
  return new Promise((resolve, reject) => {
    const eventSource = new EventSource(url);
    
    // 이벤트 리스너 등록
    eventSource.addEventListener('status', handleStatus);
    eventSource.addEventListener('result', handleResult);
    eventSource.addEventListener('error', handleError);
  });
};
```

**특징**:
- 브라우저 네이티브 EventSource API 사용
- Promise로 래핑하여 async/await 지원
- 자동 재연결 기능 내장

### 2. 상태 메시지 UI

**위치**: `frontend/src/components/MessageList.jsx`

```jsx
{message.type === 'status' ? (
  <div className="flex items-center gap-3">
    <div className="status-icon pulse-animation">🧠</div>
    <div className="text-sm font-medium">{message.content}</div>
  </div>
) : ...}
```

**상태별 아이콘**:
- 🚀 started - 시작
- 🔍 searching - 검색 중
- ⚖️ evaluating - 평가 중
- 🧠 deep_search - 깊이 검색 중 (특별 강조!)
- ✍️ generating - 답변 생성 중
- ✅ completed - 완료

### 3. 애니메이션

**위치**: `frontend/src/App.css`

```css
/* 일반 회전 애니메이션 */
.spin-animation {
  animation: gentle-spin 2s linear infinite;
}

/* 깊이 검색 펄스 애니메이션 (특별 강조) */
.pulse-animation {
  animation: deep-search-pulse 2s ease-in-out infinite;
}
```

## 🎯 사용자 경험 개선

### Before (기존 방식)
```
사용자: 질문 전송
        ↓
      [로딩 스피너 20초]
        ↓
      최종 답변 표시
```

**문제점**: 왜 오래 걸리는지 알 수 없음

### After (SSE 방식)
```
사용자: 질문 전송
        ↓
    🔍 FDA 문서를 검색하고 있습니다... (2초)
        ↓
    ⚖️ 검색 결과를 평가하고 있습니다... (1초)
        ↓
    🧠 깊이 검색중... 정확한 답변을 찾고 있습니다 (15초)
    "이 작업은 15-20초 정도 소요될 수 있습니다."
        ↓
    ✅ 추가 정보 수집 완료
        ↓
    ✍️ 최종 답변을 생성하고 있습니다... (2초)
        ↓
    최종 답변 표시
```

**개선점**: 
- ✅ 각 단계를 실시간으로 확인 가능
- ✅ "깊이 검색" 단계에서 왜 오래 걸리는지 이해
- ✅ 투명하고 신뢰할 수 있는 경험
- ✅ 사용자 이탈 감소

## 🧪 테스트

### 백엔드 테스트

```bash
# 백엔드 실행
cd backend
python main.py

# 다른 터미널에서 테스트 스크립트 실행
python test_sse.py
```

**예상 출력**:
```
============================================================
SSE 스트리밍 엔드포인트 테스트
============================================================

SSE 테스트 시작: 김치를 미국으로 수출하려면 어떤 규정을 확인해야 하나요?
------------------------------------------------------------
✅ SSE 연결 성공!
------------------------------------------------------------

📡 이벤트 타입: status
   상태: started
   메시지: 질문을 분석하고 있습니다...

📡 이벤트 타입: status
   상태: searching
   메시지: 📚 FDA 문서를 검색하고 있습니다...

📡 이벤트 타입: status
   상태: evaluating
   메시지: ⚖️ 검색 결과를 평가하고 있습니다...

📡 이벤트 타입: status
   상태: deep_search
   메시지: 🧠 깊이 검색중... 정확한 답변을 찾고 있습니다

...
```

### 프론트엔드 테스트

```bash
# 프론트엔드 실행
cd frontend
npm start

# 브라우저 개발자 도구 콘솔에서 확인:
# 1. "SSE 연결 시작" 로그
# 2. 각 이벤트별 로그
# 3. 화면에 상태 메시지 표시
```

## 🔒 에러 처리

### 1. 백엔드 에러

```python
except Exception as e:
    yield {
        "type": "error",
        "data": {
            "message": f"처리 중 오류가 발생했습니다: {str(e)}",
            "timestamp": time.time()
        }
    }
```

### 2. 네트워크 에러

```javascript
eventSource.addEventListener('error', (e) => {
  console.error('SSE 에러:', e);
  // 상태 메시지를 에러 메시지로 변경
  eventSource.close();
  // 기존 API로 폴백
});
```

### 3. 폴백 전략

SSE 실패 시 자동으로 기존 POST API로 폴백:

```javascript
try {
  await sendMessageSSE(...);
} catch (error) {
  // SSE 실패 시 기존 API 사용
  const apiResponse = await callChatAPI(...);
}
```

## 📦 필요한 패키지

### 백엔드
```txt
sse-starlette>=1.6.5
```

### 프론트엔드
없음 (브라우저 네이티브 EventSource API 사용)

## 🎉 예상 효과

### 기술적 이점
- ✅ 실시간 피드백 제공
- ✅ 자동 재연결 기능
- ✅ 타임아웃 감소
- ✅ 확장 가능한 구조

### 사용자 경험
- ✅ 빠른 질문: 3~5초 (검색 → 답변)
- ✅ 복잡한 질문: 20~25초 (검색 → 깊이 검색 → 답변)
- ✅ "왜 오래 걸리는지" 이해 가능
- ✅ 투명하고 신뢰할 수 있는 경험

## 🔄 기존 API 유지

기존 POST `/api/chat` 엔드포인트는 그대로 유지됩니다:
- 기존 코드와 호환성 유지
- 점진적 마이그레이션 가능
- SSE 미지원 클라이언트 대비

## 📚 참고 자료

- [MDN - Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [sse-starlette 문서](https://github.com/sysid/sse-starlette)
- [EventSource API](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)

