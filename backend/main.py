# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional, List
import os
from dotenv import load_dotenv
import logging
import json
import asyncio

from utils.agent import FDAAgent
import time
from datetime import datetime
from utils.orchestrator import initialize_global_services
from sse_starlette import EventSourceResponse

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="FDA Export Assistant API - ReAct Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:3001"
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [기존 유지] 기본 FDA Agent (fallback용)
try:
    fda_agent = FDAAgent()
    logger.info("FDA ReAct Agent initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize FDA Agent: {e}")
    fda_agent = None

# [추가] 프로젝트별 에이전트 딕셔너리
project_agents: Dict[int, FDAAgent] = {}

class ChatRequest(BaseModel):
    message: str
    project_id: Optional[int] = None
    language: Optional[str] = None

class ChatResponse(BaseModel):
    content: str
    keywords: List[str] = []
    cfr_references: List[Dict] = []
    sources: List[str] = []
    citations: List[Dict] = []  # ← 이 줄 추가!
    # 시간 정보
    responseTime: float = 0
    agentResponseTime: float = 0
    timestamp: str = ""

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 전역 서비스 초기화"""
    initialize_global_services()
    print("🚀 서버 준비 완료!")

@app.get("/")
async def root():
    return {"message": "FDA Export Assistant API - ReAct Agent"}

def _extract_citations(text: str) -> Dict[str, List]:
    """Very lightweight citation extractor to surface common CFR links."""
    links = []
    sources = []
    keywords = []

    mapping = [
        ("21 CFR 117", "21 CFR 117 - CGMP, Hazard Analysis, and Risk-Based Preventive Controls", "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-117", ["HACCP", "preventive controls", "CGMP"]),
        ("FSVP", "21 CFR 1 Subpart L - Foreign Supplier Verification Programs (FSVP)", "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-1/subpart-L", ["FSVP", "importer verification"]),
        ("21 CFR 101", "21 CFR 101 - Food Labeling", "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-101", ["labeling", "nutrition facts"]),
        ("GRAS", "GRAS Notice Inventory", "https://www.fda.gov/food/generally-recognized-safe-gras/gras-notice-inventory", ["GRAS"]),
    ]

    lower = text.lower()
    for key, title, url, kws in mapping:
        if key.lower() in lower:
            links.append({"title": title, "description": "관련 규정/자료", "url": url})
            sources.append(title)
            keywords.extend(kws)

    return {"cfr_references": links, "sources": sources, "keywords": list(dict.fromkeys(keywords))}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # 요청 시작 시간
    request_start_time = time.time()

    if not fda_agent:
        raise HTTPException(status_code=500, detail="Agent is not available.")
    
    try:
        project_id = request.project_id
        
        # 프로젝트 ID가 있으면 프로젝트별 에이전트 사용, 없으면 기본 에이전트 사용
        if project_id:
            if project_id not in project_agents:
                project_agents[project_id] = FDAAgent()
                logger.info(f"새 프로젝트 에이전트 생성: {project_id}")
            
            agent = project_agents[project_id]
            logger.info(f"프로젝트 {project_id}에서 질문 처리: {request.message}")
        else:
            # 기존 방식: 전역 에이전트 사용 (하위 호환성)
            agent = fda_agent
            logger.info(f"기본 에이전트로 질문 처리: {request.message}")
        
        # 에이전트 실행 시간 측정
        agent_start_time = time.time()
        agent_response = agent.chat(request.message)
        agent_end_time = time.time()
        
        logger.info("Agent generated a response.")
        
        # agent_response is a dict with content and optional citations
        content = agent_response.get("content") if isinstance(agent_response, dict) else str(agent_response)
        keywords = agent_response.get("keywords", []) if isinstance(agent_response, dict) else []
        cfr_references = agent_response.get("cfr_references", []) if isinstance(agent_response, dict) else []
        sources = agent_response.get("sources", []) if isinstance(agent_response, dict) else []
        
        # 🆕 대화 히스토리를 메모리에 저장 (후속 질문 처리를 위함)
        agent.memory.add_message(role="user", content=request.message)
        agent.memory.add_message(role="assistant", content=content)
        
        # Fallback simple extractor if missing
        if not cfr_references:
            extracted = _extract_citations(content)
            keywords = keywords or extracted.get("keywords", [])
            cfr_references = extracted.get("cfr_references", [])
            sources = sources or extracted.get("sources", [])
        
        total_response_time = (time.time() - request_start_time) * 1000
        agent_response_time = (agent_end_time - agent_start_time) * 1000
        
        return ChatResponse(
            content=content,
            keywords=keywords,
            cfr_references=cfr_references,
            sources=sources,
            citations=agent_response.get("citations", []),  # ← 이 줄 추가!
            responseTime=total_response_time,
            agentResponseTime=agent_response_time,
            timestamp=datetime.now().isoformat(),
        )
        
    except ValueError as e:
        # 에러 발생 시에도 시간 기록
        error_response_time = (time.time() - request_start_time) * 1000
        logger.warning(f"Handled error in agent: {e}")
        return ChatResponse(
            content=str(e),
            responseTime=error_response_time,
            timestamp=datetime.now().isoformat(),
        )
        
    except Exception as e:
        error_response_time = (time.time() - request_start_time) * 1000
        logger.error(f"Error processing agent chat request: {e}", exc_info=True)
        
        # 사용자 친화적 에러 메시지
        error_message = "죄송합니다. 요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        return ChatResponse(
            content=error_message,
            responseTime=error_response_time,
            timestamp=datetime.now().isoformat(),
        )

@app.delete("/api/project/{project_id}")
async def delete_project(project_id: int):
    """프로젝트 삭제 시 해당 에이전트도 제거"""
    if project_id in project_agents:
        del project_agents[project_id]
        logger.info(f"프로젝트 {project_id} 에이전트 삭제 완료")
    return {"message": "프로젝트가 삭제되었습니다."}

@app.post("/api/project/{project_id}/reset")
async def reset_project_conversation(project_id: int):
    """특정 프로젝트의 대화 히스토리 초기화"""
    if project_id in project_agents:
        project_agents[project_id].reset_conversation()
        logger.info(f"프로젝트 {project_id} 대화 히스토리 초기화 완료")
        return {"message": "대화 히스토리가 초기화되었습니다."}
    else:
        # 해당 프로젝트가 없으면 새로 생성
        project_agents[project_id] = FDAAgent()
        logger.info(f"프로젝트 {project_id} 새 에이전트 생성")
        return {"message": "새로운 대화가 시작되었습니다."}

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
        final_response_content = None  # 최종 답변 저장용
        
        try:
            # Agent의 chat_stream 메서드를 호출
            async for event in agent.chat_stream(query):
                event_type = event.get("type", "message")
                event_data = event.get("data", {})
                
                # 최종 답변 저장 (result 이벤트에서)
                if event_type == "result":
                    final_response_content = event_data.get("content", "")
                
                # SSE 형식으로 전송
                yield {
                    "event": event_type,
                    "data": json.dumps(event_data, ensure_ascii=False)
                }
                
                # token 이벤트는 지연 없이 즉시 전송, 나머지는 안정성을 위해 소폭 지연
                if event_type != "token":
                    await asyncio.sleep(0.01)
            
            # 대화 히스토리를 메모리에 저장 (후속 질문 처리를 위함)
            if final_response_content:
                agent.memory.add_message(role="user", content=query)
                agent.memory.add_message(role="assistant", content=final_response_content)
                logger.info(f"프로젝트 {project_id}: 메모리에 대화 저장 완료")
                
        except Exception as e:
            logger.error(f"SSE 스트리밍 오류: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": f"처리 중 오류가 발생했습니다: {str(e)}"
                }, ensure_ascii=False)
            }
    
    return EventSourceResponse(event_generator())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)