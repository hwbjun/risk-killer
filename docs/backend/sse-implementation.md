# SSE (Server-Sent Events) 구현 가이드

## 개요

SSE를 사용하여 백엔드 처리 과정(검색 / 평가 / 에이전트 라우팅 / 답변 생성)을 프론트엔드에 실시간으로 전달하는 피드백 시스템이 구현되었습니다.

### 기존 문제점
- 사용자가 20~30초간 "문서를 찾고 있어요" 문구만 보게 됨
- 처리 과정이 투명하지 않아 답답함을 느낄 수 있음

### 개선 사항
- 각 단계별 상태 메시지를 실시간으로 표시
- 사용자가 왜 오래 걸리는지 이해하며 기다릴 수 있도록 개선
- 처리 과정의 투명성 향상

---

## 백엔드 구현

### 1. 패키지 설치 (`backend/requirements.txt`)

```txt
# SSE (Server-Sent Events) 지원
sse-starlette>=2.1.3
```

**설치 방법:**
```bash
cd backend
pip install sse-starlette
```

### 2. SSE 엔드포인트 (`backend/main.py`)

#### 임포트 추가
```python
import json
import asyncio
from sse_starlette import EventSourceResponse
```

#### SSE 스트리밍 엔드포인트
```python
@app.get("/api/chat/stream")
async def chat_stream(query: str, project_id: Optional[int] = None):
    """SSE를 사용한 스트리밍 채팅 엔드포인트"""
    if not fda_agent:
        raise HTTPException(status_code=500, detail="Agent is not available.")
    
    logger.info(f"SSE 스트리밍 요청: query='{query}', project_id={project_id}")
    
    # 프로젝트별 에이전트 선택
    if project_id:
        if project_id not in project_agents:
            project_agents[project_id] = FDAAgent()
            logger.info(f"새 프로젝트 에이전트 생성: {project_id}")
        agent = project_agents[project_id]
    else:
        agent = fda_agent
    
    async def event_generator():
        """SSE 이벤트를 생성하는 비동기 제너레이터"""
        try:
            # Agent의 chat_stream 메서드를 호출
            async for event in agent.chat_stream(query):
                event_type = event.get("type", "message")
                event_data = event.get("data", {})
                
                # SSE 형식으로 전송
                yield {
                    "event": event_type,
                    "data": json.dumps(event_data, ensure_ascii=False)
                }
                
                # 작은 지연 추가 (안정성)
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"SSE 스트리밍 오류: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": f"처리 중 오류가 발생했습니다: {str(e)}"
                }, ensure_ascii=False)
            }
    
    return EventSourceResponse(event_generator())
```

**엔드포인트 URL:**
- `GET /api/chat/stream?query={질문}&project_id={프로젝트ID}`
- project_id는 선택적 파라미터

---

### 3. Agent의 `chat_stream` 메서드 (`backend/utils/agent.py`)

#### 처리 단계별 이벤트

1. **started** - 질문 분석 시작
2. **searching** - FDA 문서 검색 중
3. **evaluating** - 검색 결과 평가 중
4. **generating** - 답변 생성 중 (빠른 경로)
5. **deep_search** - 깊이 검색 중 (느린 경로, ReAct Agent 사용)
6. **agent_complete** - 추가 정보 수집 완료
7. **generating** - 최종 답변 생성 중
8. **completed** - 답변 생성 완료

#### 이벤트 타입

**Status 이벤트:**
```json
{
  "type": "status",
  "data": {
    "status": "searching",
    "message": "📚 FDA 문서를 검색하고 있습니다...",
    "timestamp": 1234567890.123
  }
}
```

**Result 이벤트:**
```json
{
  "type": "result",
  "data": {
    "content": "답변 내용...",
    "citations": [...],
    "keywords": [...],
    "sources": [...]
  }
}
```

**Error 이벤트:**
```json
{
  "type": "error",
  "data": {
    "message": "처리 중 오류가 발생했습니다: ...",
    "timestamp": 1234567890.123
  }
}
```

---

## 프론트엔드 구현

### 1. SSE 클라이언트 (`frontend/src/App.js`)

#### SSE 상태 관리
```javascript
const [isUsingSSE, setIsUsingSSE] = useState(false); // SSE 사용 여부 추적
```

#### SSE 연결 및 이벤트 처리
```javascript
const sendMessageSSE = (message, projectId, updatedMessages) => {
  return new Promise((resolve, reject) => {
    const query = encodeURIComponent(message);
    const projectParam = projectId ? `&project_id=${projectId}` : '';
    const url = `${getApiUrl()}/api/chat/stream?query=${query}${projectParam}`;
    
    console.log('SSE 연결 시작:', url);
    
    // SSE 사용 표시
    setIsUsingSSE(true);
    
    const eventSource = new EventSource(url);
    
    // 상태 메시지를 저장할 임시 변수
    let currentStatusMessage = null;
    let finalResponse = null;
    
    // 상태 이벤트 리스너
    eventSource.addEventListener('status', (e) => {
      const data = JSON.parse(e.data);
      // 상태 메시지 업데이트
      // ...
    });
    
    // 결과 이벤트 리스너
    eventSource.addEventListener('result', (e) => {
      const data = JSON.parse(e.data);
      // 최종 답변 표시
      // ...
      setIsUsingSSE(false);
      eventSource.close();
      resolve(finalResponse);
    });
    
    // 에러 이벤트 리스너
    eventSource.addEventListener('error', (e) => {
      console.error('SSE 에러:', e);
      setIsUsingSSE(false);
      eventSource.close();
      reject(new Error('SSE 연결 오류'));
    });
  });
};
```

#### 메시지 전송 로직
```javascript
const sendMessage = async () => {
  // ... (사용자 메시지 추가)
  
  try {
    // SSE를 사용한 스트리밍 호출
    await sendMessageSSE(message, activeProject.id, updatedMessages);
    
  } catch (error) {
    // SSE 실패 시 기존 API로 폴백
    console.log('SSE 실패, 기존 API로 폴백 시도...');
    const apiResponse = await callChatAPI(message, activeProject.id);
    // ...
  }
};
```

---

### 2. MessageList 컴포넌트 (`frontend/src/components/MessageList.jsx`)

#### SSE 상태 메시지 표시

```jsx
{message.type === 'status' ? (
  // SSE 상태 메시지 표시
  <div className="flex items-center gap-3">
    {/* 점 애니메이션 */}
    <div className="typing-dots">
      <span></span><span></span><span></span>
    </div>
    
    {/* 상태별 아이콘 */}
    <div className="status-icon pulse-animation">
      {message.status === 'searching' && '🔍'}
      {message.status === 'evaluating' && '⚖️'}
      {message.status === 'deep_search' && '🧠'}
      {message.status === 'generating' && '✍️'}
      {/* ... */}
    </div>
    
    <div className="flex-1">
      <div className="text-sm font-medium text-gray-700">
        {message.content}
      </div>
      {message.status === 'deep_search' && (
        <div className="text-xs text-gray-500 mt-1">
          이 작업은 15-20초 정도 소요될 수 있습니다.
        </div>
      )}
    </div>
  </div>
) : (
  // 일반 메시지 표시
)}
```

#### 기존 로딩 박스 숨김
```jsx
{/* 실시간 타이머 로딩 (SSE 미사용 시에만 표시) */}
{isTyping && !isUsingSSE && (
  <div className="flex justify-start">
    {/* 기존 로딩 UI */}
  </div>
)}
```

---

### 3. CSS 애니메이션 (`frontend/src/App.css`)

#### 상태 아이콘 애니메이션
```css
.status-icon {
  font-size: 1.25rem;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  min-height: 24px;
}

.pulse-animation {
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.7;
    transform: scale(1.05);
  }
}
```

#### 타이핑 점 애니메이션
```css
.typing-dots {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  background-color: #9CA3AF;
  border-radius: 50%;
  animation: typing-bounce 1.4s infinite ease-in-out;
}

.typing-dots span:nth-child(1) { 
  animation-delay: -0.32s; 
}

.typing-dots span:nth-child(2) { 
  animation-delay: -0.16s; 
}

.typing-dots span:nth-child(3) { 
  animation-delay: 0s; 
}

@keyframes typing-bounce {
  0%, 80%, 100% {
    transform: scale(0.6) translateY(0px);
    opacity: 0.4;
  }
  40% {
    transform: scale(1) translateY(-3px);
    opacity: 1;
  }
}
```

---

## 처리 흐름도

```
사용자 질문 입력
    ↓
[started] 질문 분석 시작
    ↓
제품 질문? / 일반 질문?
    ↓
[searching] FDA 문서 검색 중
    ↓
병렬 검색 실행 (BM25 + Vector + Hybrid)
    ↓
[evaluating] 검색 결과 평가 중
    ↓
충분한가?
    ↙         ↘
[YES]         [NO]
    ↓           ↓
[generating] [deep_search] ReAct Agent 추가 수집
    ↓           ↓
직접 답변   [agent_complete] 정보 수집 완료
생성           ↓
    ↓       [generating] 최종 답변 생성
    ↓           ↓
    └─────┬─────┘
          ↓
    [result] 최종 답변 전송
          ↓
    [completed] 완료
```

---

## 사용자 경험 개선

### Before (기존)
```
사용자: "김치 수출 규정이 뭐야?"
시스템: "문서를 찾고 있어요..." (20~30초 대기)
        ⏳ 15.3s 경과
시스템: [답변 표시]
```

### After (SSE 적용)
```
사용자: "김치 수출 규정이 뭐야?"
시스템: 🚀 질문을 분석하고 있습니다...
        ↓
        🔍 FDA 문서를 검색하고 있습니다...
        ↓
        ⚖️ 검색 결과를 평가하고 있습니다...
        ↓
        ✍️ 답변을 생성하고 있습니다...
        ↓
        ✅ 답변 생성 완료
        [답변 표시]
```

---

## 폴백 메커니즘

SSE 연결이 실패하는 경우, 자동으로 기존 POST API로 폴백됩니다:

```javascript
try {
  await sendMessageSSE(message, projectId, updatedMessages);
} catch (error) {
  console.log('SSE 실패, 기존 API로 폴백 시도...');
  const apiResponse = await callChatAPI(message, projectId);
  // 기존 방식으로 처리
}
```

---

## 브라우저 호환성

**SSE 지원 브라우저:**
- Chrome/Edge: ✅
- Firefox: ✅
- Safari: ✅
- Opera: ✅
- IE 11 이하: ❌ (기존 API로 자동 폴백)

**모바일 브라우저:**
- iOS Safari: ✅
- Android Chrome: ✅

---

## 테스트 방법

### 1. 백엔드 서버 시작
```bash
cd backend
python main.py
```

### 2. 프론트엔드 서버 시작
```bash
cd frontend
npm start
```

### 3. 테스트 시나리오

**빠른 경로 (병렬 검색만):**
- 질문: "FSVP가 뭐야?"
- 예상 단계: started → searching → evaluating → generating → completed

**느린 경로 (Agent 추가 수집):**
- 질문: "김치 수출하려면 어떤 서류가 필요해?"
- 예상 단계: started → searching → evaluating → deep_search → agent_complete → generating → completed

### 4. 디버깅

**백엔드 로그 확인:**
```
INFO:     SSE 스트리밍 요청: query='FSVP가 뭐야?', project_id=None
📦 제품 질문 감지: None
🔍 일반 질문 감지 - LLM 증강 적용
✨ 증강된 쿼리: FSVP...
📚 검색할 컬렉션: ['guidance', 'ecfr', 'fsvp']
⚡ 병렬 검색 완료: 1.23초, 8개 결과
✅ 병렬 검색 결과만으로 충분 - 직접 답변 생성
```

**프론트엔드 콘솔 확인:**
```
SSE 연결 시작: http://localhost:8000/api/chat/stream?query=...
Status 이벤트: {status: "started", message: "질문을 분석하고 있습니다..."}
Status 이벤트: {status: "searching", message: "FDA 문서를 검색하고 있습니다..."}
Result 이벤트: {content: "...", citations: [...]}
```

---

## 주의사항

1. **SSE 연결 제한**: 브라우저당 최대 6개의 동시 SSE 연결 제한이 있습니다. (HTTP/1.1 기준)
2. **타임아웃**: 서버 또는 프록시에서 장시간 연결을 끊을 수 있으므로, 주기적으로 heartbeat를 전송하는 것이 좋습니다. (현재 구현에서는 0.01초마다 이벤트 전송)
3. **에러 핸들링**: SSE 연결 실패 시 자동으로 폴백되도록 구현되어 있습니다.
4. **메모리 관리**: EventSource 객체는 사용 후 반드시 `close()`로 닫아야 메모리 누수를 방지할 수 있습니다.

---

## 향후 개선 사항

1. **진행률 표시**: 각 단계별 진행률(%)을 표시
2. **재연결 로직**: SSE 연결이 끊긴 경우 자동 재연결
3. **Heartbeat**: 장시간 연결 유지를 위한 주기적 heartbeat 이벤트
4. **취소 기능**: 사용자가 생성 중인 답변을 중단할 수 있는 기능
5. **상세 로그**: 각 검색 컬렉션별 결과 개수 표시

---

## 문의 및 지원

문제가 발생하거나 개선 사항이 있으면 이슈를 등록해주세요.

