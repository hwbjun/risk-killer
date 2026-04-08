# utils/agent.py
"""
ReAct 프레임워크를 사용하여 FDA 규제 질문에 답변하는 메인 에이전트.
"""
import os
import json
import re
import time
import logging
from typing import List, Dict
from llama_index.core.agent.workflow import AgentWorkflow
from llama_index.llms.openai import OpenAI
from llama_index.core import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
import asyncio

from utils.tools import create_fda_tools
from utils.memory import ConversationMemory, ChatMessage
from utils.collection_strategy import COLLECTION_STRATEGY

# ReAct Agent 상세 로깅을 위한 설정
# 기본 로깅 레벨은 INFO로 설정 (DEBUG는 너무 많음)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# LlamaIndex ReAct Agent만 DEBUG로 설정 (Thought, Action, Observation 로그를 위해)
logging.getLogger("llama_index.agent.react").setLevel(logging.DEBUG)

# 불필요한 DEBUG 로그 제거
logging.getLogger("openai").setLevel(logging.WARNING)  # OpenAI HTTP 요청/응답 로그 제거
logging.getLogger("httpx").setLevel(logging.WARNING)  # HTTP 요청 로그 제거
logging.getLogger("httpcore").setLevel(logging.WARNING)  # HTTP 코어 로그 제거
logging.getLogger("llama_index.core.indices").setLevel(logging.WARNING)  # LlamaIndex 인덱스 상세 로그 제거
logging.getLogger("sse_starlette").setLevel(logging.WARNING)  # SSE 디버그 로그 제거

class FDAAgent:
    def __init__(self):
        # LlamaIndex 전역 설정 (rag_engine과 동일하게 설정)
        Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small", api_key=os.getenv("OPENAI_API_KEY"))
        Settings.llm = OpenAI(
            model="gpt-4.1",
            temperature=0,
            api_key=os.getenv("OPENAI_API_KEY"),
            max_tokens=2000
        )

        self.response_llm = OpenAI(
            model="gpt-5.2",
            temperature=0,
            api_key=os.getenv("OPENAI_API_KEY"),
            max_tokens=8000,
            reasoning_effort="low"
        )

        # 1. 모든 FDA 컬렉션을 '전문가 툴'로 변환
        self.fda_tools = create_fda_tools()

        # 멀티턴 대화를 위한 메모리 추가
        self.memory = ConversationMemory()
        
        # 제품 분해 캐시 추가
        self.decomposition_cache = {}
        
        # 🆕 검색 결과 캐시 추가 (후속 질문 지원)
        self.search_results_cache = []  # 최근 검색 결과 저장

        # 컬렉션 라우팅 기본값 및 보조 LLM
        self.available_collections = ['guidance', 'ecfr', 'gras', 'dwpe', 'fsvp', 'rpm', 'usc']
        self.default_collections = ['guidance', 'ecfr', 'gras', 'dwpe']
        self.collection_classifier_llm = OpenAI(model="gpt-4o-mini", temperature=0)
        
        # ⚡ Orchestrator를 한 번만 생성 (속도 최적화)
        # - BM25 캐시 재사용
        # - Reranker 모델 재사용 (모델 로딩 시간 절약)
        from utils.orchestrator import SimpleOrchestrator
        self.orchestrator = SimpleOrchestrator()
        print("✅ Orchestrator 초기화 완료 (BM25 + Reranker 준비됨)")

        # ✅ [수정] 에이전트의 행동 방식을 정의하는 새로운 시스템 프롬프트 (정보 수집 전용)
        system_prompt = """당신은 FDA 규제 정보 수집 전문가입니다.

## 역할
사용자 질문에 답하기 위해 필요한 정보를 도구로 수집하세요.
최종 답변은 생성하지 마세요. 정보만 수집하세요.

## 🆕 법적 권한 질문 특별 처리 (최우선!)
"Can FDA...", "Does FDA have...", "Who determines..." 질문 시:

**Step 0: 질문의 핵심 동사 파악 (가장 중요!)**
질문에서 FDA가 할 수 있는지 물어보는 **정확한 행위**를 파악하세요:
- "add to the list" = "alter the list" = "change the list" = "modify the statutory list"
  → 이것은 statutory list 변경을 의미 (major allergen list)
- "require labeling" ≠ "add to the list"
  → 라벨링 요구는 리스트 추가와 **완전히 다른 권한**

**핵심:** "add X to the list"는 "alter/change the statutory list"와 **동일**합니다!

**Step 1: 키워드 우선순위 검색 (필수)**
검색된 문서에서 다음 순서로 키워드를 찾으세요:

**우선순위 1 (질문과 직접 대응):**
- 질문: "add/change/alter/modify the list" 
  → 문서: "cannot alter the statutory list" ✅ 이것이 답!
  
**우선순위 2 (부정 키워드):**
- "cannot", "can not", "unable to", "prohibited"
- "Congress determines", "statutory", "by law"
- "Section 201(qq)", "FD&C Act"

**우선순위 3 (긍정 키워드 - 주의!):**
- "can require labeling" ← 이것은 다른 권한! 리스트 추가 권한이 아님!

**Step 2: 정보 수집 형식**
```
**질문 분석:**
- "add to the list" = "alter the statutory list" (동일한 의미)

**법적 권한 (핵심):**
- [키워드 발견]: "FDA cannot alter the statutory list" (출처: [N])
- 법적 근거: Section 201(qq) - Congress determines
- **결론: No, FDA cannot add allergens to the statutory list**

**별도 권한 (보충 정보):**
- [키워드 발견]: "can require labeling for other allergens"
- 범위: 비주요 알레르겐(non-major allergens)만
- 주의: 이것은 라벨링 요구 권한이지, 리스트 추가 권한이 아님!
```

**Step 3: 혼동 방지 규칙 (일반 원칙)**
1. **"cannot" 키워드 최우선**
   - 문서에 "cannot", "unable to", "prohibited" 발견 → 무조건 "No"
   - 다른 긍정 표현("can do X")이 있어도 무시

2. **질문의 동사와 문서의 동사 일치 확인**
   - 질문: "add/change/alter/modify" → 문서: "cannot alter" → 일치! → No
   - 질문: "add to list" → 문서: "can require labeling" → 불일치! → 다른 권한

3. **법적 권한의 주체 확인**
   - "Congress determines", "statutory", "by law" → FDA 권한 없음
   - "Section XXX of Act" → 법으로 정해진 것

4. **리스트 vs 라벨링 vs 승인 구분**
   - Statutory list (법정 목록) ≠ Labeling requirement (라벨링 요구)
   - Direct authority (직접 권한) ≠ Recommendation (권고)
   
**일반 패턴:**
```
질문: "Can FDA [VERB] [OBJECT]?"
문서 검색:
  1. "cannot [VERB]" 발견 → No (우선!)
  2. "Congress/statutory determines [OBJECT]" → FDA 권한 없음 → No
  3. "can [OTHER_VERB]" 발견 → 동사 일치 여부 확인
```

## 수집해야 할 정보
1. CFR 규정 (구체적 번호 + 내용)
2. Import Alert 확인
3. 라벨링 요구사항
4. FSVP/검증 절차
5. **법적 근거 (21 USC, Section 201(qq))** 
6. 기타 관련 규제 정보

## 출력 형식
수집한 정보를 구조화된 형식으로 정리하세요:

**법적 근거:** ⬅️ 우선 순위 1
- [USC/법 조항]: [내용]

**CFR 규정:**
- [규정 번호]: [내용]

**Import Alert:**
- [Alert 번호]: [내용]

**라벨링:**
- [요구사항]

**FSVP:**
- [절차]

## 중요
- 제공된 검색 결과를 우선 참고하세요
- 부족한 정보만 도구로 추가 검색하세요
- 한국어 쿼리는 반드시 영어로 변환하세요
- **"cannot"과 "can"을 명확히 구분하세요** ⬅️ 강조

## 도구 사용 강제 케이스
다음 키워드 포함 시 무조건 도구 사용:
- "비용", "cost", "payment", "supervision", "누가"
- "절차", "procedure", "process", "어떻게"
- "Chapter", "Section", "GRN", "CFR", "USC"
- "규정", "regulation", "requirement"
- "relabeling", "detention", "import", "GRAS"
- **"can fda", "does fda", "authority", "권한", "who determines"** ⬅️ 추가

**도구 회피 금지:**
❌ "(Implicit) I can answer without tools" → 절대 금지
❌ "일반적으로 알려진 바로는..." → 금지
✅ 반드시: Action → Observation → Answer 순서

## 최상위 규칙 (Golden Rule)
- 절대 사전 지식만으로 답변하지 마세요. 반드시 도구를 사용하여 검색하세요.
- **한국어 쿼리는 반드시 영어로 변환하여 도구에 전달하세요.**
- **도구를 선택하기 전에 쿼리를 분석하세요.**
- **"cannot"이 있으면 무조건 "No"입니다!** ⬅️ 핵심 추가
"""

        # 2. ReAct 에이전트 생성 (AgentWorkflow 기반)
        # context 내용을 system_prompt 끝에 병합
        system_prompt += """

## 도구 사용 강제 (CRITICAL)
You MUST use tools for FDA-related queries.
NEVER answer with "(Implicit) I can answer without tools".
For keywords like "비용/cost", "절차/procedure", "Chapter", "relabeling" → ALWAYS use tools.
Always translate Korean to English before searching.

## 도구 호출 제한 (CRITICAL)
- 도구 호출은 최대 10회까지만 허용됩니다.
- 같은 도구에 유사한 쿼리를 반복하지 마세요.
- 충분한 정보를 수집했으면 즉시 답변을 생성하세요."""

        self.agent = AgentWorkflow.from_tools_or_functions(
            tools_or_functions=self.fda_tools,
            llm=Settings.llm,
            system_prompt=system_prompt,
            timeout=90.0,
            verbose=True,
        )

    def _run_agent(self, query: str) -> str:
        """AgentWorkflow를 동기 컨텍스트에서 실행하는 헬퍼"""
        async def _exec():
            handler = self.agent.run(user_msg=query)
            result = await handler
            return str(result)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(lambda: asyncio.run(_exec())).result()
        else:
            return asyncio.run(_exec())

    def _is_food_export_question_llm(self, query: str) -> bool:
        """
        빠르고 저렴한 LLM(gpt-4o-mini)을 사용하여 사용자의 질문이
        '특정 식품의 수출 규제'에 대한 것인지 분류하는 필터 함수.
        """
        try:
            # 필터 전용으로 저렴한 모델을 임시로 사용
            filter_llm = OpenAI(model="gpt-4o-mini", temperature=0)
            
            prompt = f"""
            Is the following user query about the regulations for exporting a specific food item?
            Answer ONLY with "Yes" or "No". Do not add any other text, explanation, or punctuation.

            Query: "{query}"
            """
            
            response = filter_llm.complete(prompt)
            answer = response.text.strip().lower()
            
            print(f"LLM Filter Check for query '{query}': Answer='{answer}'") # 디버깅용 로그
            
            return answer == "yes"

        except Exception as e:
            print(f"LLM Filter failed: {e}") # 에러 로그
            return False # 에러 발생 시 안전하게 False로 처리

    def _decompose_product(self, product_name: str) -> dict:
        """제품 분해 (10개 요소) - 한국 음식 지원 강화"""
        # 캐시 확인
        if product_name in self.decomposition_cache:
            return self.decomposition_cache[product_name]
        
        # 한국어 감지 및 처리 지침 추가
        is_korean = any(ord(char) >= 0xAC00 and ord(char) <= 0xD7A3 for char in product_name)
        
        if is_korean:
            prompt_prefix = f"""
You are analyzing a KOREAN food product. First, identify what '{product_name}' is in English.
Common Korean foods:
- 떡볶이 = Tteokbokki (spicy rice cakes)
- 김치 = Kimchi (fermented cabbage)
- 김밥 = Kimbap (rice rolls with vegetables)
- 만두 = Mandu (dumplings)
- 불고기 = Bulgogi (marinated beef)
- 비빔밥 = Bibimbap (mixed rice bowl)
- If not listed above, translate and identify the components.

Now analyze '{product_name}' for FDA requirements.
"""
        else:
            prompt_prefix = f"Analyze '{product_name}' for FDA requirements."
        
        decomposition_prompt = f"""{prompt_prefix}
        
Return a JSON object with EXACTLY these fields:
{{
  "ingredients": [list of main components in English],
  "processes": [manufacturing/cooking methods],
  "allergens": [only FDA major allergens if present: milk, eggs, fish, shellfish, tree nuts, peanuts, wheat, soybeans, sesame],
  "origin": "Korea" if Korean food else appropriate country,
  "category": "ethnic food" if Korean else appropriate category,
  "subcategories": [relevant subcategories],
  "storage_type": "frozen", "refrigerated", or "ambient",
  "risk_level": "high", "medium", or "low",
  "packaging_concerns": [relevant concerns],
  "potential_hazards": [food safety hazards],
  "import_type": "commercial" or "personal use"
}}

Examples:
- For 떡볶이: {{"ingredients": ["rice cake", "fish cake", "gochujang"], ...}}
- For 김치: {{"ingredients": ["cabbage", "chili powder", "garlic"], ...}}

Return ONLY valid JSON, no other text or markdown.
"""
        
        try:
            response = Settings.llm.complete(decomposition_prompt)
            text = response.text.strip()
            
            # Markdown 코드 블록 제거
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            
            # JSON 파싱
            decomposition = json.loads(text)
            
            # 필드 검증 및 기본값 추가
            defaults = {
                "ingredients": [],
                "processes": [],
                "allergens": [],
                "origin": "Korea" if is_korean else "unknown",
                "category": "ethnic food" if is_korean else "food",
                "subcategories": [],
                "storage_type": "ambient",
                "risk_level": "medium",
                "packaging_concerns": [],
                "potential_hazards": [],
                "import_type": "commercial"
            }
            
            # 누락된 필드 채우기
            for key, default_value in defaults.items():
                if key not in decomposition or not decomposition[key]:
                    decomposition[key] = default_value
            
            # 캐싱
            self.decomposition_cache[product_name] = decomposition
            return decomposition
            
        except (json.JSONDecodeError, Exception) as e:
            print(f"Decomposition failed for '{product_name}': {e}")
            print(f"LLM Response: {response.text if 'response' in locals() else 'No response'}")
            
            # 스마트한 폴백: LLM 한 번 더 시도 (더 간단한 방식)
            try:
                simple_prompt = f"""
What are the main ingredients of {product_name}?
Answer in this exact format:
ingredients: item1, item2, item3
allergens: allergen1, allergen2
"""
                simple_response = Settings.llm.complete(simple_prompt)
                lines = simple_response.text.strip().split('\n')
                
                ingredients = []
                allergens = []
                
                for line in lines:
                    if line.startswith('ingredients:'):
                        ingredients = [i.strip() for i in line.split(':')[1].split(',')]
                    elif line.startswith('allergens:'):
                        allergens = [a.strip() for a in line.split(':')[1].split(',')]
                
                return {
                    "ingredients": ingredients or [product_name],
                    "processes": ["processing", "packaging"],
                    "allergens": allergens,
                    "origin": "Korea" if is_korean else "unknown",
                    "category": "ethnic food" if is_korean else "food",
                    "subcategories": ["imported food"],
                    "storage_type": "refrigerated" if is_korean else "ambient",
                    "risk_level": "medium",
                    "packaging_concerns": ["labeling required"],
                    "potential_hazards": ["contamination"],
                    "import_type": "commercial"
                }
                
            except:
                # 최종 폴백
                return {
                    "ingredients": [product_name],
                    "processes": [],
                    "allergens": [],
                    "origin": "Korea" if is_korean else "unknown",
                    "category": "ethnic food" if is_korean else "food",
                    "subcategories": [],
                    "storage_type": "ambient",
                    "risk_level": "medium",
                    "packaging_concerns": [],
                    "potential_hazards": [],
                    "import_type": "commercial"
                }

    def _extract_product_name(self, query: str) -> str:
        """LLM을 사용하여 쿼리에서 제품명 추출"""
        prompt = f"""
Analyze this user query and determine if it contains a FOOD PRODUCT name.

Query: "{query}"

Rules:
- Return ONLY the food product name if one exists
- Return "None" if this is a general question (about regulations, procedures, concepts)
- Examples of products: "김치", "새우튀김", "냉동만두", "chicken nuggets"
- Examples of NOT products: "HACCP", "FDA", "규정", "절차", "라벨링"

Answer with ONLY the product name or "None":
"""
        
        try:
            response = Settings.llm.complete(prompt)
            result = response.text.strip()
            
            # "None" 또는 "none" 반환 시 None으로 변환
            if result.lower() == "none":
                return None
            
            return result
            
        except Exception as e:
            print(f"LLM product extraction failed: {e}")
            # 에러 시 안전하게 None 반환
            return None

    def _augment_general_query(self, original_query: str) -> str:
        """일반 질문에 대한 LLM 쿼리 증강 (대화 컨텍스트 포함)"""
        
        # 🆕 대화 히스토리 가져오기
        conversation_context = self.memory.get_recent_context_summary()
        
        # 🆕 후속 질문 감지
        followup_indicators = ["그러면", "그럼", "그것", "그거", "그걸", "누구", "어디", "왜", "언제", 
                               "then", "who", "where", "why", "when", "which", "what about"]
        is_followup = any(indicator in original_query.lower() for indicator in followup_indicators)
        
        # 🆕 이전 답변에서 "statutory" 키워드 발견 + Who 질문이면 특별 처리
        is_who_question = any(w in original_query.lower() for w in ["누구", "who", "권한"])
        has_statutory_in_history = False
        
        if is_followup and is_who_question and self.memory.messages:
            # 최근 어시스턴트 답변 확인
            for msg in reversed(self.memory.messages):
                if msg.role == "assistant":
                    if "statutory" in msg.content.lower() or "법적으로 정의" in msg.content:
                        has_statutory_in_history = True
                        break
        
        # 컨텍스트가 있고 후속 질문인 경우
        if conversation_context and is_followup:
            # 🆕 statutory + who 질문이면 특별 키워드 추가
            special_instruction = ""
            if has_statutory_in_history:
                special_instruction = """
⚠️ 중요: 이전 답변에서 "statutory" 또는 "법적으로 정의"라는 표현이 있었습니다.
이것은 법으로 정해진 것을 의미하므로, 검색 쿼리에 다음을 반드시 포함하세요:
- "Congress" (의회)
- "legislative authority" (입법 권한)
- "Section 201(qq)" (관련 법 조항)
- "statutory definition" (법적 정의)
"""
            
            prompt = f"""
이전 대화:
{conversation_context}

현재 사용자 질문: {original_query}

⚠️ 이 질문은 이전 대화를 참조하는 후속 질문입니다.
{special_instruction}

다음 단계를 따라 검색 쿼리를 생성하세요:
1. 이전 대화에서 논의된 주요 주제/개념 파악
2. 현재 질문이 참조하는 대상("그것", "누구" 등) 식별
3. 이전 맥락을 포함한 **완전한 독립적 검색 쿼리**로 변환 (영어로)
4. FDA 규제 맥락에 맞는 전문 용어 추가
5. 관련 동의어 및 키워드 확장

예시 (패턴):
- 이전: "Can X do Y?"
- 현재: "그러면 누구에게 권한이 있나요"
- 검색 쿼리: "Who has the authority to [Y] [관련 법적 용어] statutory determination legal basis regulatory authority Congress"

실제 예시:
- 이전: "시설 등록이 필요한가요?"
- 현재: "누가 승인하나요?"
- 검색 쿼리: "Who approves facility registration FDA regional office authority registration process approval requirements"

변환된 검색 쿼리만 반환하세요 (설명 없이):
"""
        else:
            # 일반 질문 (컨텍스트 없음)
            prompt = f"""
다음 사용자 질문을 FDA 규제 데이터베이스 검색에 최적화된 영어 쿼리로 변환하고 확장하세요.

사용자 질문: {original_query}

⚠️ 중요: 검색 커버리지를 위해 원본 질문의 핵심 키워드를 반드시 포함하세요!

다음 요소들을 포함하여 검색 쿼리를 생성하세요:
1. **원본 질문의 핵심 키워드 보존** (특히 "can", "add", "allergens" 같은 핵심 동사/명사)
2. 핵심 키워드를 영어로 변환
3. 관련 동의어 및 전문 용어 추가
4. FDA 규제 맥락에 맞는 검색어 확장
5. **질문 유형에 따른 균형잡힌 키워드 추가:**
   - 권한/제한 질문 ("Can", "Does", "Who determines"): 법적 근거 키워드 필수
     → "cannot", "authority", "Congress", "statutory", "Section 201", "FD&C Act"
   - 정의 질문 ("What is"): 최신 정보 키워드 포함
     → "sesame", "nine allergens", "FASTER Act 2021"
   - 둘 다 해당하면: 법적 근거 + 최신 정보 모두 포함

예시:
- "비용이 얼마나 드나요?" 
  → "비용 얼마 costs payment fees supervision relabeling expenses"
  (원본 키워드 "비용" 포함!)
  
- "What is a major food allergen?" 
  → "what is major food allergen definition nine allergens milk eggs fish shellfish tree nuts peanuts wheat soybeans sesame FASTER Act 2021"
  (원본 키워드 "what is", "major food allergen" 포함!)
  
- "Can FDA add allergens to the list?"
  → "Can FDA add allergens list authority statutory Congress cannot alter Section 201 FD&C Act regulatory power legislative FASTER Act sesame nine major food allergen"
  (원본 키워드 "Can FDA add allergens list" 모두 포함 + 법적 근거 + 최신 정보)
  
- "Who determines major food allergens?"
  → "who determines major food allergens Congress statutory authority Section 201 legislative power FD&C Act cannot alter FDA role"
  (원본 키워드 "who determines major food allergens" 모두 포함!)

변환된 검색 쿼리만 반환하세요 (설명 없이):
"""
        
        try:
            response = Settings.llm.complete(prompt)
            augmented_query = response.text.strip()
            
            # 🆕 후속 질문인 경우 디버깅 출력
            if is_followup:
                print(f"\n🔗 후속 질문 감지!")
                print(f"  - 원본: {original_query}")
                if has_statutory_in_history:
                    print(f"  - 🏛️ Statutory 감지 - Congress 키워드 추가됨")
                print(f"  - 증강: {augmented_query}")
            
            # 원본 쿼리와 증강된 쿼리 결합
            return f"{original_query}\n\nEnhanced search query: {augmented_query}"
            
        except Exception as e:
            print(f"Query augmentation failed: {e}")
            return original_query

    def _augment_query(self, original_query: str, decomposition: dict) -> str:
        """분해된 10개 요소를 모두 활용하는 쿼리 증강"""
        augmented = f"""
User Question: {original_query}

PRODUCT ANALYSIS (10 elements):
1. Ingredients: {', '.join(decomposition.get('ingredients', []))}
2. Processes: {', '.join(decomposition.get('processes', []))}  
3. Allergens: {', '.join(decomposition.get('allergens', []))}
4. Origin: {decomposition.get('origin', 'unknown')}
5. Category: {decomposition.get('category', 'food')}
6. Subcategories: {', '.join(decomposition.get('subcategories', []))}
7. Storage: {decomposition.get('storage_type', 'ambient')}
8. Risk Level: {decomposition.get('risk_level', 'medium')}
9. Packaging Concerns: {', '.join(decomposition.get('packaging_concerns', []))}
10. Potential Hazards: {', '.join(decomposition.get('potential_hazards', []))}

SEARCH STRATEGY (7 collections):
1. guidance: 실무 가이드 (CPG, 라벨링, 알레르기)
2. ecfr: 구체적 규정 (21 CFR)
3. gras: 재료 안전성 확인
4. dwpe: Import Alert 확인
5. fsvp: 수입자 검증 의무
6. rpm: 수입 운영 절차
7. usc: 법적 기반 (21 USC)

Use the most relevant collections based on the product characteristics above.
"""
        return augmented

    def _extract_citations_from_response(self, response) -> dict:
        """Extract citations (title/url) from LlamaIndex response source nodes."""
        citations = []
        sources = []
        keywords = []
        try:
            if hasattr(response, 'source_nodes') and response.source_nodes:
                for node in response.source_nodes:
                    meta = getattr(node, 'metadata', {}) or {}
                    title = meta.get('title') or meta.get('document_title') or meta.get('collection') or 'Reference'
                    url = meta.get('url') or meta.get('source') or meta.get('link')
                    description = meta.get('summary') or meta.get('description') or '관련 규정/자료'
                    if url:
                        item = {"title": title, "description": description, "url": url}
                        if item not in citations:
                            citations.append(item)
                            sources.append(title)
                    # derive simple keywords from tool/collection
                    tool_name = meta.get('tool_name') or meta.get('collection')
                    if tool_name:
                        keywords.append(tool_name)
        except Exception:
            pass
        return {"cfr_references": citations, "sources": sources, "keywords": list(dict.fromkeys(keywords))}

    def _extract_legal_references(self, results: List[Dict]) -> List[str]:
        """검색 결과에서 법 조항/섹션 번호 추출"""
        references = []
        
        for result in results[:5]:  # 상위 5개만 확인
            text = result.get('text', '') + ' ' + result.get('title', '')
            
            # 정규식으로 법 조항 추출
            # Section 201(qq), 21 CFR 101, 21 U.S.C. 343 등
            patterns = [
                r'Section\s+\d+\([a-z]+\)',  # Section 201(qq)
                r'\d+\s+CFR\s+\d+\.\d+',     # 21 CFR 101.22
                r'\d+\s+CFR\s+\d+',          # 21 CFR 101
                r'\d+\s+U\.?S\.?C\.?\s+§?\s*\d+',  # 21 USC 343 또는 21 U.S.C. § 343
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                references.extend(matches)
        
        # 중복 제거 및 정규화
        seen = set()
        unique_refs = []
        for ref in references:
            ref_normalized = ref.strip().replace('  ', ' ')
            if ref_normalized not in seen:
                seen.add(ref_normalized)
                unique_refs.append(ref_normalized)
        
        return unique_refs[:5]  # 상위 5개만
    
    def _format_parallel_results(self, results: List[Dict]) -> str:
        """병렬 검색 결과를 텍스트로 포맷"""
        if not results:
            return "병렬 검색 결과 없음"
        
        formatted = []
        for i, result in enumerate(results[:10], 1):  # 5 → 10개로 증가
            formatted.append(f"""
{i}. [{result['score']:.2f}] {result['collection']} {result.get('collection_role', '')}
   제목: {result.get('title', 'N/A')}
   내용: {result.get('text', 'N/A')[:2000]}...  # 200 → 800자로 증가
   출처: {result.get('collection_desc', '')}
""")
        
        return "\n".join(formatted)

    def chat(self, query: str) -> dict:
        """사용자 제안 구조: 제품 질문은 분해, 일반 질문은 LLM 증강"""
        try:
            product = self._extract_product_name(query)
            
            if product:
                # 제품 질문: 분해 방식
                print(f"📦 제품 질문 감지: {product}")
                decomposition = self._decompose_product(product)
                search_query = query  # 원본 사용
                print(f"🔬 제품 분해 완료: {decomposition.get('category')}")
            else:
                # 일반 질문: LLM 증강 방식
                print("🔍 일반 질문 감지 - LLM 증강 적용")
                decomposition = None
                search_query = self._augment_general_query(query)  # 여기서 증강!
                print(f"✨ 증강된 쿼리: {search_query[:100]}...")
                classification = self._classify_question(query)
                collections = self._select_collections(classification)
                print(f"🧭 질문 분류 결과: {classification}")
            
            # ⚡ 재사용 가능한 orchestrator 사용 (매번 생성하지 않음 - 속도 향상!)
            orchestrator = self.orchestrator
            
            if decomposition:
                # 제품 질문: 분해 기반 컬렉션 선택
                collections = orchestrator.determine_collections(decomposition)
            else:
                # 일반 질문: 기본 컬렉션 사용
                # 상단에서 분류된 컬렉션이 없는 경우를 대비한 폴백
                collections = collections if 'collections' in locals() else self.default_collections
            
            print(f"📚 검색할 컬렉션: {collections}")
            
            # 병렬 검색 실행
            parallel_results = orchestrator.parallel_search(
                query=search_query,  # 증강된 또는 원본
                collections=collections,
                decomposition=decomposition
            )
            
            ranked_results = orchestrator.merge_and_rank(parallel_results)
            print(f"⚡ 병렬 검색 완료: {parallel_results['search_time']:.2f}초, {len(ranked_results)}개 결과")
            
            # 🆕 검색 결과 캐싱
            self.search_results_cache.append({
                'query': query,
                'results': ranked_results,
                'timestamp': time.time()
            })
            
            # 최근 2개만 유지 (메모리 관리)
            if len(self.search_results_cache) > 2:
                self.search_results_cache.pop(0)
            
            # 🆕 후속 질문이고 이전 검색 결과가 있으면 병합
            followup_indicators = ["그러면", "그럼", "그것", "그거", "그걸", "누구", "어디", "왜", "언제", 
                                   "then", "who", "where", "why", "when", "which", "what about"]
            is_followup = any(indicator in query.lower() for indicator in followup_indicators)
            
            if is_followup and len(self.search_results_cache) >= 2:
                print(f"\n🔗 후속 질문 감지 - 이전 검색 결과 재사용")
                previous_results = self.search_results_cache[-2]['results']
                current_results = ranked_results
                
                # 🆕 이전 검색에서 사용한 컬렉션 재사용
                previous_collections = list(set(r['collection'] for r in previous_results))
                if previous_collections != collections:
                    print(f"  🔄 이전 컬렉션 재사용: {previous_collections}")
                    collections = list(set(collections + previous_collections))
                
                # 🆕 권한 관련 질문 감지
                authority_keywords = ["권한", "누구", "who", "can", "cannot", "authority", "determines"]
                is_authority_question = any(kw in query.lower() for kw in authority_keywords)
                
                # 🆕 이전 결과에서 고품질 문서 추출 (score > 0.5로 낮춤)
                high_quality_docs = [r for r in previous_results if r.get('score', 0) > 0.5]
                
                # 🆕 권한 질문이면 authority 키워드 포함 문서 우선
                if is_authority_question:
                    authority_docs = [
                        r for r in previous_results 
                        if any(kw in r.get('text', '').lower() for kw in 
                               ["authority", "congress", "statutory", "cannot", "must", "legislative"])
                    ]
                    if authority_docs:
                        print(f"  🏛️ 권한 관련 문서 발견: {len(authority_docs)}개")
                        # authority 문서를 최우선으로
                        high_quality_docs = authority_docs + [d for d in high_quality_docs if d not in authority_docs]
                
                if high_quality_docs:
                    print(f"  ⭐ 이전 고품질 문서: {len(high_quality_docs)}개 (점수 > 0.5)")
                    for doc in high_quality_docs[:5]:
                        print(f"     - {doc.get('title', 'N/A')[:50]}... (점수: {doc.get('score', 0):.3f})")
                
                # 🆕 이전 결과에서 법 조항/섹션 추출
                legal_references = self._extract_legal_references(previous_results)
                if legal_references:
                    print(f"  📜 이전 문서에서 추출된 법 조항: {', '.join(legal_references[:3])}")
                    
                    if len(current_results) < 3:
                        print(f"  🔍 현재 결과 부족({len(current_results)}개) - 법 조항으로 재검색")
                        additional_query = " ".join(legal_references[:2])
                        additional_results = orchestrator.parallel_search(
                            query=additional_query,
                            collections=collections,
                            decomposition=decomposition
                        )
                        additional_ranked = orchestrator.merge_and_rank(additional_results)
                        print(f"  ✅ 추가 검색 완료: {len(additional_ranked)}개 결과")
                        current_results = list(current_results) + list(additional_ranked[:5])
                
                # 🆕 병합 전략: 고품질 문서 우선 + 이전 상위 결과 더 많이 + 현재 결과
                merged_results = []
                # 고품질 문서 최대 5개
                merged_results.extend(high_quality_docs[:5])
                
                # 이전 결과에서 더 많이 가져오기 (5개 → 8개)
                for r in previous_results[:8]:
                    if r not in merged_results:
                        merged_results.append(r)
                        if len(merged_results) >= 8:
                            break
                
                merged_results.extend(current_results)
                
                # 중복 제거 (더 관대한 기준)
                seen_keys = set()
                unique_results = []
                for r in merged_results:
                    title = r.get('title', '')[:50]
                    text = r.get('text', '')[:50]  # 100 → 50으로 변경 (더 많은 유사 문서 허용)
                    key = f"{title}|{text}"
                    
                    if key not in seen_keys:
                        seen_keys.add(key)
                        unique_results.append(r)
                
                ranked_results = unique_results[:12]  # 10 → 12개로 증가
                print(f"  ✅ 병합 완료: 고품질 {len(high_quality_docs[:5])}개 + 이전 {min(8-len(high_quality_docs[:5]), len(previous_results[:8]))}개 + 현재 {len(current_results)}개 → 총 {len(ranked_results)}개")
            
            # 결과 충분성 평가 및 응답 생성
            if self._is_parallel_result_sufficient(ranked_results, decomposition or {}):
                # decomposition 있든 없든, 충분하면 직접 답변
                print("✅ 병렬 검색 결과만으로 충분 - 직접 답변 생성")
                return self._generate_direct_response(query, ranked_results, decomposition)
            else:
                # ReAct Agent로 추가 정보 수집
                print("🔄 ReAct Agent로 추가 정보 수집")
                search_summary = self._format_parallel_results(ranked_results)
                
                if decomposition:
                    enhanced_query = f"""
{self._augment_query(query, decomposition)}

## 검색된 FDA 문서들
{search_summary}

위 정보를 활용하고, 부족한 부분만 추가 검색하세요.
정보 수집만 하고, 최종 답변은 생성하지 마세요.
"""
                else:
                    enhanced_query = f"""
{search_query}

## 검색된 FDA 문서들
{search_summary}

위 정보를 활용하고, 부족한 부분만 추가 검색하세요.
정보 수집만 하고, 최종 답변은 생성하지 마세요.
"""
                
                context = self.memory.get_context_for_agent()
                full_query = f"{context}\n{enhanced_query}" if context else enhanced_query
                
                # Agent로 정보 수집만
                print("🔍 Agent 정보 수집 시작...")

                try:
                    collected_info = self._run_agent(full_query)

                    print("="*80)
                    print("✅ ReAct Agent 실행 완료")
                    print(f"📝 수집된 정보 길이: {len(collected_info)}자")
                    print("="*80)
                except Exception as e:
                    print(f"❌ ReAct Agent 실행 중 오류: {e}")
                    import traceback
                    traceback.print_exc()
                    raise

                # 병렬 검색 + Agent 정보를 합쳐서 최종 답변 생성
                print("✅ 정보 수집 완료 - 최종 답변 생성")
                return self._generate_response_with_agent_info(
                    query=query,
                    parallel_results=ranked_results,
                    agent_info=collected_info,
                    decomposition=decomposition
                )
            
        except Exception as e:
            print(f"Error in chat: {e}")
            fallback = self._generate_fallback_response(query)
            return {
                "content": fallback,
                "cfr_references": [],
                "sources": [],
                "keywords": []
            }

    def _classify_question(self, query: str) -> dict:
        """LLM을 활용하여 질문 유형과 적합한 컬렉션을 동적으로 결정"""
        prompt = f"""
You are an assistant that routes FDA-related questions to the most relevant document collections.

Available collections:
- guidance (guidance documents, policy interpretations, FAQs)
- ecfr (21 CFR regulations)
- gras (GRAS notices and ingredient safety)
- dwpe (Import Alerts, detention without physical examination)
- fsvp (Foreign Supplier Verification Program)
- rpm (Regulatory Procedures Manual)
- usc (21 U.S.C. legal statutes)

For the given question, choose the 2-4 most relevant collections and classify the question.
Return ONLY valid JSON in this format without any extra text:
{{
  "category": "DEFINITION | PROCEDURE | COMPLIANCE | PRODUCT | ENFORCEMENT | OTHER",
  "collections": ["collection_name", ...],
  "reason": "Short Korean explanation for logging"
}}

Rules:
- Always pick from the provided collection names.
- Include "guidance" for 정의/FAQ/labeling 질문.
- Include "ecfr" for regulatory requirements, definitions, labeling rules, allergen regulations, or when CFR numbers are mentioned.
- Include "dwpe" for import alert or detention topics.
- Include "fsvp" for importer responsibilities or verification.
- Include "usc" for legal authority or penalties.
- Include "gras" for GRAS notices, ingredient safety evaluations, or food additive status inquiries only.
- Include "rpm" only for procedural import handling questions.
- If multiple collections are relevant, list them in order of importance.
- Never output an empty list.

Question: "{query}"
"""

        for attempt in range(2):
            try:
                response = self.collection_classifier_llm.complete(prompt)
                raw = response.text.strip()

                # 코드 블록 제거
                if raw.startswith("```"):
                    raw = raw.strip("`").strip()
                    if raw.lower().startswith('json'):
                        raw = raw[4:].strip()

                classification = json.loads(raw)

                collections = classification.get('collections', [])
                classification['collections'] = self._sanitize_collections(collections)

                if not classification['collections']:
                    classification['collections'] = self.default_collections

                return classification

            except Exception as e:
                print(f"Question classification attempt {attempt + 1} failed: {e}")
                continue

        return {"category": "OTHER", "collections": self.default_collections, "reason": "fallback"}

    def _sanitize_collections(self, collections: List[str]) -> List[str]:
        """허용된 컬렉션만 남기고 중복 제거"""
        if not collections:
            return []
        seen = set()
        sanitized = []
        for coll in collections:
            if coll in self.available_collections and coll not in seen:
                sanitized.append(coll)
                seen.add(coll)
        return sanitized

    def _select_collections(self, classification: dict) -> List[str]:
        """분류 결과를 기반으로 실제 사용할 컬렉션 결정"""
        if not classification:
            return self.default_collections

        collections = classification.get('collections') or []
        sanitized = self._sanitize_collections(collections)

        if sanitized:
            return sanitized

        return self.default_collections

    def _is_parallel_result_sufficient(self, results: List[Dict], decomposition: dict) -> bool:
        """병렬 검색 결과의 충분성 평가 (단순화된 품질 중심 기준)"""
        print(f"\n🔍 충분성 평가 시작")
        print(f"  - 전체 결과 개수: {len(results)}")
        
        if not results or len(results) < 2:
            print(f"  ❌ 결과 부족: {len(results)}개")
            return False
        
        avg_score = sum(r['score'] for r in results) / len(results)
        max_score = max(r['score'] for r in results)
        print(f"  - 평균 점수: {avg_score:.3f} (임계값: 0.65)")
        print(f"  - 최고 점수: {max_score:.3f}")
        
        if avg_score < 0.65:
            unique_collections = set(r['collection'] for r in results)
            if max_score >= 0.75 and len(results) >= 2:
                print(f"  ✅ 예외 통과: 고품질 결과 (최고 {max_score:.3f})")
                return True
            print(f"  ❌ 평균 점수 부족")
            return False
        
        unique_collections = set(r['collection'] for r in results)
        print(f"  - 컬렉션 다양성: {len(unique_collections)}개 {list(unique_collections)}")
        print(f"  ✅ 충분성 평가 통과!\n")
        return True

    def _generate_direct_response(self, query: str, results: List[Dict], decomposition: dict) -> dict:
        """병렬 검색 결과만으로 직접 답변 생성 (제품 질문과 일반 질문 모두 지원)"""
        
        # 출처 번호 매핑 생성
        citations = []
        for i, r in enumerate(results[:8], 1):
            # 제목이 비어있거나 None인 경우 기본값 설정
            title = r.get('title', '').strip()
            if not title:
                title = f"{r['collection'].upper()} Document {i}"

            # URL이 비어있는 경우 기본 URL 생성
            url = r.get('url', '').strip()
            if not url:
                # 컬렉션별 기본 URL 생성
                if r['collection'] == 'fsvp':
                    url = f"https://www.fda.gov/food/importing-food-products-united-states/foreign-suppliers-verification-programs-fsvp-importer-portal-records-submission"
                elif r['collection'] == 'gras':
                    url = f"https://www.hfpappexternal.fda.gov/scripts/fdcc/index.cfm?set=GRASNotices"
                elif r['collection'] == 'ecfr':
                    url = f"https://www.ecfr.gov/current/title-21"
                elif r['collection'] == 'guidance':
                    url = f"https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
                elif r['collection'] == 'dwpe':
                    url = f"https://www.accessdata.fda.gov/cms_ia/country_KR.html"
                elif r['collection'] == 'usc':
                    url = f"https://www.law.cornell.edu/uscode/text/21"

            citations.append({
                "index": i,
                "collection": r['collection'],
                "title": title,
                "url": url,
                "score": r['score'],
                "content": r.get('text', '')  # 평가용: 문서 내용 추가
            })

        # 출처 리스트 (프롬프트용)
        source_list = "\n".join([
            f"[출처 {c['index']}] {c['collection']}: {c['title'][:80]}"
            for c in citations
        ])

        # 전체 검색 결과를 풍부하게 전달
        full_context = "\n\n".join([
            f"[출처 {i+1}] {r['collection'].upper()} (점수: {r['score']:.3f})\n"
            f"제목: {r.get('title', 'N/A')}\n"
            f"내용: {r.get('text', '')[:3000]}"
            for i, r in enumerate(results[:8])
        ])
        
        # 🆕 디버깅 출력 추가
        print("\n" + "="*60)
        print("🔍 검색된 문서 내용 (디버깅)")
        print("="*60)
        for i, r in enumerate(results[:2], 1):
            text_content = r.get('text', '')
            print(f"\n[출처 {i}] {r['collection'].upper()} (점수: {r['score']:.3f})")
            print(f"제목: {r.get('title', 'N/A')}")
            print(f"📏 원본 텍스트 길이: {len(text_content)}자")  # ⬅️ 핵심!
            print(f"내용 (첫 500자): {text_content[:2000]}")
            print()
        print("="*60 + "\n")
        
        if decomposition:
            prompt = f"""
사용자 질문: {query}

제품 특성:
{json.dumps(decomposition, indent=2, ensure_ascii=False)}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{full_context}

## 출처 목록
{source_list}

위 문서들을 종합하여 다음 사항을 포함한 답변을 작성하세요:
1. 구체적인 CFR 규정 번호와 내용
2. Import Alert 여부
3. 알레르기 라벨링 구체적 요구사항
4. FSVP 검증 절차
5. 실무 체크리스트

**🚨 Citation 규칙 (필수! 반드시 준수!):**
- **답변의 모든 주요 주장 뒤에 반드시 [1], [2] 형식으로 출처 번호를 표시하세요.**
- 여러 출처를 참고한 경우 [1][2] 처럼 연속으로 표시하세요.
- **출처 번호 없이는 답변을 완료하지 마세요.**
- **각 문단마다 최소 1개 이상의 출처 번호가 있어야 합니다.**

**Citation 예시 (반드시 이 형식을 따르세요):**
- 새우는 주요 알레르기 유발 물질로 표시해야 합니다[1].
- 21 CFR 1250.26과 Import Alert 16-50을 준수해야 합니다[2][3].
- 주요 식품 알레르겐은 9가지입니다[1][2]. 이들은 우유, 계란, 생선, 갑각류 해산물, 견과류, 땅콩, 밀, 콩, 참깨입니다[1][2].

**Citation 체크리스트 (답변 완료 전 확인):**
✓ 답변의 각 문단에 출처 번호가 있는가?
✓ 주요 규정이나 법 조항을 언급할 때 출처 번호가 있는가?
✓ 통계나 구체적 정보를 제시할 때 출처 번호가 있는가?

**🌏 언어 규칙 (최우선!):**
- 질문이 영어든 한국어든 상관없이 **무조건 한국어로만 답변하세요.**
- 답변에 영어를 섞지 마세요. 모든 내용을 한국어로 작성하세요.
- 예외 없이 100% 한국어로 답변하세요.
"""
        else:
            prompt = f"""
사용자 질문: {query}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{full_context}

## 출처 목록
{source_list}

위 문서들을 종합하여 답변하되, 다음 규칙을 **반드시** 따르세요:

**🚨 질문 유형 구분 (최우선!):**

**1) 5W1H 질문 (Who/What/Where/When/Why/How):**
- "누구", "무엇", "어디", "언제", "왜", "어떻게"
- "Who", "What", "Where", "When", "Why", "How"
- **❌ 절대 "예/아니요"로 시작하지 마세요!**
- **✅ 답의 핵심(누구/무엇)으로 첫 문장 시작**

**Who 질문 특별 규칙:**
질문: "누가 X를 하나요?" / "Who does X?"
- ❌ 잘못: "아니요, FDA는 X를 못합니다. Y가 합니다."
- ✅ 올바름: "Y가 X를 합니다."
- **핵심: 질문에서 물어본 대상(Who)을 첫 문장에 바로 답하세요!**

**2) Yes/No 질문 (Can/Does/Is/Will):**

**핵심 원칙:**
1. **질문의 주어를 정확히 파악하세요**
   - 질문 주어와 문서 주어가 일치하는지 확인
   - 주어가 다르면 답도 달라질 수 있음

2. **질문과 문서의 의미가 일치하는지 확인하세요**
   - 질문이 묻는 것과 문서가 답하는 것이 같은 내용인지 판단
   - 단순 키워드 매칭이 아닌 의미적 일치 여부 확인

3. **첫 문장은 "예" 또는 "아니요"로 명확히 시작하세요**
   - 애매한 표현 금지
   - 한국어로 답변 시 "예/아니요" 사용 (Yes/No 아님)

**🚨 답변 길이 규칙 (중요!):**
- **반드시 상세하고 포괄적으로 답변하세요.**
- "예/아니요"만으로 답변하지 마세요. 항상 이유와 배경을 설명하세요.
- 관련 법 조항, 규정 번호, 구체적인 절차를 포함하세요.
- 최소 3-5문단 이상의 상세한 답변을 작성하세요.

**예시로 배우기:**

예시 1:
Q: "Can FDA add allergens to the list?"
문서: "FDA cannot alter the statutory list. Congress must amend the law."
분석: 질문 주어(FDA) = 문서 주어(FDA), "cannot" 발견
A: "아니요, FDA는 목록에 알레르겐을 추가할 수 없습니다. 

FD&C 법(Federal Food, Drug, and Cosmetic Act)의 제 201(qq) 조항에 따르면, 주요 식품 알레르겐 목록은 법률로 정해진 것이며, FDA는 이 법정 목록(statutory list)을 변경할 권한이 없습니다. 현재 9가지 주요 식품 알레르겐(우유, 계란, 생선, 갑각류 해산물, 견과류, 땅콩, 밀, 콩, 참깨)은 의회(Congress)가 법을 개정해야만 변경할 수 있습니다.

다만, FDA는 주요 알레르겐으로 지정되지 않은 다른 식품 알레르겐에 대해서는, 적절한 경우 라벨링을 요구할 수 있는 권한을 가지고 있습니다. 이는 목록 자체를 변경하는 것과는 별개의 권한입니다."

예시 2:
Q: "그러면 추가 권한은 의회에게만 있나요?"
문서: "FDA cannot alter... Congress must amend the law."
분석: 질문 주어(의회) ≠ 문서 부정 주어(FDA), 문서에 "Congress must" 발견
A: "예, 주요 알레르겐 목록 변경 권한은 의회에게만 있습니다."
(주의: 문서에 "cannot"이 있어도, FDA에 대한 부정이므로 의회 질문엔 "예")

예시 3:
Q: "Is FDA required to inspect?"
문서: "FDA must inspect all facilities."
분석: 질문 주어(FDA) = 문서 주어(FDA), 긍정 의무("must")
A: "예, FDA는 모든 시설을 검사해야 합니다."

예시 4:
Q: "제조업체가 승인할 수 있나요?"
문서: "Only FDA can approve. Manufacturers cannot approve."
분석: 질문 주어(제조업체) = 문서 부정 주어(Manufacturers)
A: "아니요, 제조업체는 승인할 수 없습니다. FDA만 승인할 수 있습니다."

**답변 체크리스트:**
✓ 질문의 주어와 문서의 주어가 일치하는가?
✓ 질문이 묻는 행위와 문서의 행위가 같은가?
✓ "예/아니요"가 질문에 논리적으로 맞는가?
✓ 답변 내용이 첫 문장의 "예/아니요"와 일치하는가?

**금지 사항:**
❌ 키워드만 보고 기계적으로 답변 (예: "cannot" 발견 → 무조건 "아니요")
❌ 질문의 주어를 무시하고 답변
❌ 모순되는 답변 ("아니요, ~할 수 있습니다" 또는 "예, ~할 수 없습니다")
❌ 5W1H 질문에 "예/아니요"로 답변

**Citation 규칙:**
- 각 주장 뒤에 [1], [2] 형식으로 출처 번호 표시
- 여러 출처는 [1][2] 처럼 연속으로 표시

**📏 답변 길이 및 상세도 (필수!):**
- **최소 200-400자 이상의 상세한 답변을 작성하세요.**
- "예/아니요"만으로 끝내지 마세요. 항상 배경, 이유, 법적 근거를 포함하세요.
- 관련 규정 번호(예: 21 CFR XXX, Section 201(qq))를 구체적으로 명시하세요.
- 가능한 경우 실무적인 예시나 추가 정보를 포함하세요.
- 간결함보다는 **정확성과 완전성**을 우선하세요.

**🌏 언어 규칙 (최우선!):**
- 질문이 영어든 한국어든 상관없이 **무조건 한국어로만 답변하세요.**
- 답변에 영어를 섞지 마세요. 모든 내용을 한국어로 작성하세요.
- 예외 없이 100% 한국어로 답변하세요.
"""

        response = self.response_llm.complete(prompt)
        if not response.text.strip():
            print("⚠️ 빈 응답 감지 - 1회 재시도")
            response = self.response_llm.complete(prompt)

        print(f"\n📋 Citations 생성 완료:")
        print(f"  - 총 {len(citations)}개 citations 생성")
        for c in citations:
            print(f"    [{c['index']}] {c['collection']}: {c['title'][:50]}...")
        
        return {
            "content": response.text,
            "citations": citations,
            "cfr_references": [],
            "sources": [c['title'] for c in citations[:5]],
            "keywords": list(set(r['collection'] for r in results))
        }

    def _generate_response_with_agent_info(
        self, 
        query: str, 
        parallel_results: List[Dict],
        agent_info: str,
        decomposition: dict
    ) -> dict:
        """병렬 검색 + Agent 수집 정보를 종합하여 답변 생성"""
        
        print("\n" + "="*60)
        print("📝 최종 답변 생성 시작")
        print("="*60)
        
        # 출처 번호 매핑 생성
        citations = []
        for i, r in enumerate(parallel_results[:8], 1):
            # 제목이 비어있거나 None인 경우 기본값 설정
            title = r.get('title', '').strip()
            if not title:
                title = f"{r['collection'].upper()} Document {i}"

            # URL이 비어있는 경우 기본 URL 생성
            url = r.get('url', '').strip()
            if not url:
                # 컬렉션별 기본 URL 생성
                if r['collection'] == 'fsvp':
                    url = f"https://www.fda.gov/food/importing-food-products-united-states/foreign-suppliers-verification-programs-fsvp-importer-portal-records-submission"
                elif r['collection'] == 'gras':
                    url = f"https://www.hfpappexternal.fda.gov/scripts/fdcc/index.cfm?set=GRASNotices"
                elif r['collection'] == 'ecfr':
                    url = f"https://www.ecfr.gov/current/title-21"
                elif r['collection'] == 'guidance':
                    url = f"https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
                elif r['collection'] == 'dwpe':
                    url = f"https://www.fda.gov/import-alerts"
                elif r['collection'] == 'usc':
                    url = f"https://www.law.cornell.edu/uscode/text/21"

            citations.append({
                "index": i,
                "collection": r['collection'],
                "title": title,
                "url": url,
                "score": r['score'],
                "content": r.get('text', '')  # 평가용: 문서 내용 추가
            })

        # 출처 리스트 (프롬프트용)
        source_list = "\n".join([
            f"[출처 {c['index']}] {c['collection']}: {c['title'][:80]}"
            for c in citations
        ])

        # 병렬 검색 결과 정리
        parallel_context = "\n\n".join([
            f"[출처 {i+1}] {r['collection'].upper()} (점수: {r['score']:.3f})\n"
            f"제목: {r.get('title', 'N/A')}\n"
            f"내용: {r.get('text', '')[:3000]}"
            for i, r in enumerate(parallel_results[:8])
        ])
        
        print(f"📊 입력 정보:")
        print(f"  - 병렬 검색 결과: {len(parallel_results)}개")
        print(f"  - Agent 수집 정보: {len(agent_info)}자")
        print(f"  - 총 컨텍스트: {len(parallel_context) + len(agent_info)}자")
        
        # 통합 프롬프트
        if decomposition:
            prompt = f"""
사용자 질문: {query}

제품 특성:
{json.dumps(decomposition, indent=2, ensure_ascii=False)}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{parallel_context}

Agent가 추가 수집한 정보:
{agent_info[:3000]}

## 출처 목록
{source_list}

위 모든 정보를 종합하여 다음을 포함한 답변을 작성하세요:
1. 구체적인 CFR 규정 번호와 내용
2. Import Alert 여부
3. 알레르기 라벨링 구체적 요구사항
4. FSVP 검증 절차
5. 실무 체크리스트 (5개 이상)

**🚨 Citation 규칙 (필수! 반드시 준수!):**
- **답변의 모든 주요 주장 뒤에 반드시 [1], [2] 형식으로 출처 번호를 표시하세요.**
- 여러 출처를 참고한 경우 [1][2] 처럼 연속으로 표시하세요.
- **출처 번호 없이는 답변을 완료하지 마세요.**
- **각 문단마다 최소 1개 이상의 출처 번호가 있어야 합니다.**

**Citation 예시 (반드시 이 형식을 따르세요):**
- 새우는 주요 알레르기 유발 물질로 표시해야 합니다[1].
- 21 CFR 1250.26과 Import Alert 16-50을 준수해야 합니다[2][3].
- 주요 식품 알레르겐은 9가지입니다[1][2]. 이들은 우유, 계란, 생선, 갑각류 해산물, 견과류, 땅콩, 밀, 콩, 참깨입니다[1][2].

**Citation 체크리스트 (답변 완료 전 확인):**
✓ 답변의 각 문단에 출처 번호가 있는가?
✓ 주요 규정이나 법 조항을 언급할 때 출처 번호가 있는가?
✓ 통계나 구체적 정보를 제시할 때 출처 번호가 있는가?

**🌏 언어 규칙 (최우선!):**
- 질문이 영어든 한국어든 상관없이 **무조건 한국어로만 답변하세요.**
- 답변에 영어를 섞지 마세요. 모든 내용을 한국어로 작성하세요.
- 예외 없이 100% 한국어로 답변하세요.
"""
        else:
            prompt = f"""
사용자 질문: {query}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{parallel_context}

Agent가 추가 수집한 정보:
{agent_info[:3000]}

## 출처 목록
{source_list}

**🚨 필수 규칙: 정보 우선순위**
1. **Agent가 수집한 정보가 최신이고 정확합니다.**
2. **Agent 정보와 출처 문서가 충돌하면 무조건 Agent 정보를 따르세요!**
3. **특히 알레르겐 개수가 다르면 Agent 정보(9개, 참깨 포함)가 맞습니다.**
4. **출처 1이 8개라고 해도, Agent가 9개라고 하면 9개로 답변하세요.**

위 모든 정보를 종합하여 답변하되, 다음 규칙을 **반드시** 따르세요:

**🚨 질문 유형 구분 (최우선!):**

**1) 5W1H 질문 (Who/What/Where/When/Why/How):**
- "누구", "무엇", "어디", "언제", "왜", "어떻게"
- "Who", "What", "Where", "When", "Why", "How"
- **❌ 절대 "예/아니요"로 시작하지 마세요!**
- **✅ 답의 핵심(누구/무엇)으로 첫 문장 시작**

**Who 질문 특별 규칙:**
질문: "누가 X를 하나요?" / "Who does X?"
- ❌ 잘못: "아니요, FDA는 X를 못합니다. Y가 합니다."
- ✅ 올바름: "Y가 X를 합니다."
- **핵심: 질문에서 물어본 대상(Who)을 첫 문장에 바로 답하세요!**

예시:
- Q: "그러면 누가 그 권한을 가지고 있나요?"
- 문서: "FDA cannot alter... Congress must amend the law"
- ❌ 나쁜 답변: "아니요, FDA는 권한이 없습니다. 의회가 법을 개정해야 합니다."
- ✅ 좋은 답변: "의회(Congress)가 그 권한을 가지고 있습니다. 법률 개정을 통해서만 변경 가능합니다."

- Q: "누가 시설을 검사하나요?"
- ❌ 잘못: "아니요, 제조업체는 검사할 수 없습니다."
- ✅ 올바름: "FDA 지역 사무소가 시설을 검사합니다."

- Q: "어디에 제출해야 하나요?"
- ❌ 잘못: "아니요, 제출할 수 없습니다."
- ✅ 올바름: "FDA의 전자 제출 시스템(ESG)을 통해 제출해야 합니다."

**2) Yes/No 질문 (Can/Does/Is/Will):**

**핵심 원칙:**
1. **질문의 주어를 정확히 파악하세요**
   - 질문 주어와 문서 주어가 일치하는지 확인
   - 주어가 다르면 답도 달라질 수 있음

2. **질문과 문서의 의미가 일치하는지 확인하세요**
   - 질문이 묻는 것과 문서가 답하는 것이 같은 내용인지 판단
   - 단순 키워드 매칭이 아닌 의미적 일치 여부 확인

3. **첫 문장은 "예" 또는 "아니요"로 명확히 시작하세요**
   - 애매한 표현 금지
   - 한국어로 답변 시 "예/아니요" 사용 (Yes/No 아님)

**🚨 답변 길이 규칙 (중요!):**
- **반드시 상세하고 포괄적으로 답변하세요.**
- "예/아니요"만으로 답변하지 마세요. 항상 이유와 배경을 설명하세요.
- 관련 법 조항, 규정 번호, 구체적인 절차를 포함하세요.
- 최소 3-5문단 이상의 상세한 답변을 작성하세요.

**예시로 배우기:**

예시 1:
Q: "Can FDA add allergens to the list?"
문서: "FDA cannot alter the statutory list. Congress must amend the law."
분석: 질문 주어(FDA) = 문서 주어(FDA), "cannot" 발견
A: "아니요, FDA는 목록에 알레르겐을 추가할 수 없습니다. 

FD&C 법(Federal Food, Drug, and Cosmetic Act)의 제 201(qq) 조항에 따르면, 주요 식품 알레르겐 목록은 법률로 정해진 것이며, FDA는 이 법정 목록(statutory list)을 변경할 권한이 없습니다. 현재 9가지 주요 식품 알레르겐(우유, 계란, 생선, 갑각류 해산물, 견과류, 땅콩, 밀, 콩, 참깨)은 의회(Congress)가 법을 개정해야만 변경할 수 있습니다.

다만, FDA는 주요 알레르겐으로 지정되지 않은 다른 식품 알레르겐에 대해서는, 적절한 경우 라벨링을 요구할 수 있는 권한을 가지고 있습니다. 이는 목록 자체를 변경하는 것과는 별개의 권한입니다."

예시 2:
Q: "그러면 추가 권한은 의회에게만 있나요?"
문서: "FDA cannot alter... Congress must amend the law."
분석: 질문 주어(의회) ≠ 문서 부정 주어(FDA), 문서에 "Congress must" 발견
A: "예, 주요 알레르겐 목록 변경 권한은 의회에게만 있습니다."
(주의: 문서에 "cannot"이 있어도, FDA에 대한 부정이므로 의회 질문엔 "예")

예시 3:
Q: "Is FDA required to inspect?"
문서: "FDA must inspect all facilities."
분석: 질문 주어(FDA) = 문서 주어(FDA), 긍정 의무("must")
A: "예, FDA는 모든 시설을 검사해야 합니다."

예시 4:
Q: "제조업체가 승인할 수 있나요?"
문서: "Only FDA can approve. Manufacturers cannot approve."
분석: 질문 주어(제조업체) = 문서 부정 주어(Manufacturers)
A: "아니요, 제조업체는 승인할 수 없습니다. FDA만 승인할 수 있습니다."

**답변 체크리스트:**
✓ 질문의 주어와 문서의 주어가 일치하는가?
✓ 질문이 묻는 행위와 문서의 행위가 같은가?
✓ "예/아니요"가 질문에 논리적으로 맞는가?
✓ 답변 내용이 첫 문장의 "예/아니요"와 일치하는가?

**금지 사항:**
❌ 키워드만 보고 기계적으로 답변 (예: "cannot" 발견 → 무조건 "아니요")
❌ 질문의 주어를 무시하고 답변
❌ 모순되는 답변 ("아니요, ~할 수 있습니다" 또는 "예, ~할 수 없습니다")
❌ 5W1H 질문에 "예/아니요"로 답변

**🚨 Citation 규칙 (필수! 반드시 준수!):**
- **답변의 모든 주요 주장 뒤에 반드시 [1], [2] 형식으로 출처 번호를 표시하세요.**
- 여러 출처를 참고한 경우 [1][2] 처럼 연속으로 표시하세요.
- **출처 번호 없이는 답변을 완료하지 마세요.**
- **각 문단마다 최소 1개 이상의 출처 번호가 있어야 합니다.**

**Citation 예시 (반드시 이 형식을 따르세요):**
- 주요 식품 알레르겐은 9가지입니다[1][2]. 이들은 우유, 계란, 생선, 갑각류 해산물, 견과류, 땅콩, 밀, 콩, 참깨입니다[1][2].
- FDA는 법정 목록을 변경할 권한이 없습니다[1][3]. 의회(Congress)가 법을 개정해야만 변경 가능합니다[1][3].
- 라벨링 요구사항은 21 CFR 117.3에 명시되어 있습니다[2][4].

**Citation 체크리스트 (답변 완료 전 확인):**
✓ 답변의 각 문단에 출처 번호가 있는가?
✓ 주요 규정이나 법 조항을 언급할 때 출처 번호가 있는가?
✓ 통계나 구체적 정보를 제시할 때 출처 번호가 있는가?

**🌏 언어 규칙 (최우선!):**
- 질문이 영어든 한국어든 상관없이 **무조건 한국어로만 답변하세요.**
- 답변에 영어를 섞지 마세요. 모든 내용을 한국어로 작성하세요.
- 예외 없이 100% 한국어로 답변하세요.
"""
        
        print(f"\n🤖 LLM 호출 중... (프롬프트: {len(prompt)}자)")
        
        # 단일 LLM 호출로 최종 답변 생성
        response = self.response_llm.complete(prompt)
        if not response.text.strip():
            print("⚠️ 빈 응답 감지 - 1회 재시도")
            response = self.response_llm.complete(prompt)

        # Citation 확인 및 경고
        answer_text = response.text
        import re
        citation_pattern = r'\[\d+\]'
        found_citations = re.findall(citation_pattern, answer_text)
        
        print(f"\n✅ 최종 답변 생성 완료!")
        print(f"  - 답변 길이: {len(answer_text)}자")
        print(f"  - 답변 단어 수: {len(answer_text.split())}단어")
        print(f"  - 발견된 Citation: {len(found_citations)}개")
        
        if len(found_citations) == 0:
            print(f"\n⚠️ 경고: 답변에 Citation이 없습니다!")
            print(f"  - 최소 {len(citations)}개의 출처가 있으므로 Citation을 추가해야 합니다.")
            print(f"  - 답변 끝에 출처 정보를 추가하는 것을 고려하세요.")
        elif len(found_citations) < len(citations) // 2:
            print(f"\n⚠️ 경고: Citation이 부족합니다 (발견: {len(found_citations)}개, 출처: {len(citations)}개)")
        
        print(f"\n📋 Citations 생성 완료:")
        print(f"  - 총 {len(citations)}개 citations 생성")
        for c in citations:
            print(f"    [{c['index']}] {c['collection']}: {c['title'][:50]}...")
        
        # 최종 답변 내용 출력
        print("\n" + "="*60)
        print("📄 최종 답변 내용:")
        print("="*60)
        print(answer_text)
        print("="*60 + "\n")
        
        return {
            "content": answer_text,
            "citations": citations,
            "cfr_references": [],
            "sources": [c['title'] for c in citations[:5]],
            "keywords": list(set(r['collection'] for r in parallel_results))
        }

    def _generate_fallback_response(self, query: str) -> str:
        """검색 실패시 폴백 응답"""
        return f"""
죄송합니다. '{query}'에 대한 구체적인 정보를 데이터베이스에서 찾을 수 없습니다.

일반적으로 식품 수출 시 확인해야 할 FDA 규제 사항:

1. **제조 시설 요구사항**
   - FDA 시설 등록 (Food Facility Registration)
   - HACCP 또는 HARPC 계획 수립

2. **라벨링 규정**
   - 영양성분표 (Nutrition Facts)
   - 원재료 목록 (Ingredient List)
   - 알레르기 유발 물질 표시

3. **식품 안전 기준**
   - 미생물 한계 기준 준수
   - 잔류 농약 및 중금속 기준

더 구체적인 정보가 필요하시면 FDA 공식 웹사이트나 전문 컨설턴트 상담을 권장합니다.
"""

    def _extract_used_tools(self, response) -> List[str]:
        """응답에서 사용된 툴 목록을 추출"""
        used_tools = []
        if hasattr(response, 'source_nodes'):
            for node in response.source_nodes:
                if hasattr(node, 'metadata') and 'tool_name' in node.metadata:
                    tool_name = node.metadata['tool_name']
                    if tool_name not in used_tools:
                        used_tools.append(tool_name)
        return used_tools
    
    def reset_conversation(self):
        """대화 히스토리 초기화"""
        self.memory.clear_history()
        # 🆕 검색 결과 캐시도 초기화
        self.search_results_cache = []
        # AgentWorkflow는 run()마다 새 컨텍스트를 생성하므로 별도 리셋 불필요

    async def chat_stream(self, query: str):
        """
        SSE 스트리밍 방식으로 중간 상태와 최종 답변을 생성합니다.
        각 처리 단계마다 이벤트를 yield하여 실시간 피드백을 제공합니다.
        """
        try:
            # 시작 이벤트
            yield {
                "type": "status",
                "data": {
                    "status": "started",
                    "message": "질문을 분석하고 있습니다...",
                    "timestamp": time.time()
                }
            }
            
            # 1. 제품 질문 vs 일반 질문 분류
            product = self._extract_product_name(query)
            
            if product:
                print(f"📦 제품 질문 감지: {product}")
                decomposition = self._decompose_product(product)
                search_query = query
                print(f"🔬 제품 분해 완료: {decomposition.get('category')}")
            else:
                print("🔍 일반 질문 감지 - LLM 증강 적용")
                decomposition = None
                search_query = self._augment_general_query(query)
                print(f"✨ 증강된 쿼리: {search_query[:100]}...")
                classification = self._classify_question(query)
                collections = self._select_collections(classification)
                print(f"🧭 질문 분류 결과: {classification}")
            
            # 2. 검색 시작 이벤트
            yield {
                "type": "status",
                "data": {
                    "status": "searching",
                    "message": "📚 FDA 문서를 검색하고 있습니다...",
                    "timestamp": time.time()
                }
            }
            
            # 3. Orchestrator를 통한 병렬 검색
            orchestrator = self.orchestrator
            
            if decomposition:
                collections = orchestrator.determine_collections(decomposition)
            else:
                collections = collections if 'collections' in locals() else self.default_collections
            
            print(f"📚 검색할 컬렉션: {collections}")
            
            parallel_results = orchestrator.parallel_search(
                query=search_query,
                collections=collections,
                decomposition=decomposition
            )
            
            ranked_results = orchestrator.merge_and_rank(parallel_results)
            print(f"⚡ 병렬 검색 완료: {parallel_results['search_time']:.2f}초, {len(ranked_results)}개 결과")
            
            # 🆕 검색 결과 캐싱
            self.search_results_cache.append({
                'query': query,
                'results': ranked_results,
                'timestamp': time.time()
            })
            
            # 최근 2개만 유지
            if len(self.search_results_cache) > 2:
                self.search_results_cache.pop(0)
            
            # 🆕 후속 질문 처리 (chat()과 동일한 로직)
            followup_indicators = ["그러면", "그럼", "그것", "그거", "그걸", "누구", "어디", "왜", "언제", 
                                   "then", "who", "where", "why", "when", "which", "what about"]
            is_followup = any(indicator in query.lower() for indicator in followup_indicators)
            
            if is_followup and len(self.search_results_cache) >= 2:
                print(f"\n🔗 후속 질문 감지 - 이전 검색 결과 재사용")
                previous_results = self.search_results_cache[-2]['results']
                current_results = ranked_results
                
                # 🆕 이전 검색에서 사용한 컬렉션 재사용
                previous_collections = list(set(r['collection'] for r in previous_results))
                if previous_collections != collections:
                    print(f"  🔄 이전 컬렉션 재사용: {previous_collections}")
                    collections = list(set(collections + previous_collections))
                
                # 🆕 권한 관련 질문 감지
                authority_keywords = ["권한", "누구", "who", "can", "cannot", "authority", "determines"]
                is_authority_question = any(kw in query.lower() for kw in authority_keywords)
                
                # 🆕 이전 결과에서 고품질 문서 추출 (score > 0.5로 낮춤)
                high_quality_docs = [r for r in previous_results if r.get('score', 0) > 0.5]
                
                # 🆕 권한 질문이면 authority 키워드 포함 문서 우선
                if is_authority_question:
                    authority_docs = [
                        r for r in previous_results 
                        if any(kw in r.get('text', '').lower() for kw in 
                               ["authority", "congress", "statutory", "cannot", "must", "legislative"])
                    ]
                    if authority_docs:
                        print(f"  🏛️ 권한 관련 문서 발견: {len(authority_docs)}개")
                        # authority 문서를 최우선으로
                        high_quality_docs = authority_docs + [d for d in high_quality_docs if d not in authority_docs]
                
                if high_quality_docs:
                    print(f"  ⭐ 이전 고품질 문서: {len(high_quality_docs)}개 (점수 > 0.5)")
                    for doc in high_quality_docs[:5]:
                        print(f"     - {doc.get('title', 'N/A')[:50]}... (점수: {doc.get('score', 0):.3f})")
                
                # 🆕 이전 결과에서 법 조항/섹션 추출
                legal_references = self._extract_legal_references(previous_results)
                if legal_references:
                    print(f"  📜 이전 문서에서 추출된 법 조항: {', '.join(legal_references[:3])}")
                    
                    if len(current_results) < 3:
                        print(f"  🔍 현재 결과 부족({len(current_results)}개) - 법 조항으로 재검색")
                        additional_query = " ".join(legal_references[:2])
                        additional_results = orchestrator.parallel_search(
                            query=additional_query,
                            collections=collections,
                            decomposition=decomposition
                        )
                        additional_ranked = orchestrator.merge_and_rank(additional_results)
                        print(f"  ✅ 추가 검색 완료: {len(additional_ranked)}개 결과")
                        current_results = list(current_results) + list(additional_ranked[:5])
                
                # 🆕 병합 전략: 고품질 문서 우선 + 이전 상위 결과 더 많이 + 현재 결과
                merged_results = []
                # 고품질 문서 최대 5개
                merged_results.extend(high_quality_docs[:5])
                
                # 이전 결과에서 더 많이 가져오기 (5개 → 8개)
                for r in previous_results[:8]:
                    if r not in merged_results:
                        merged_results.append(r)
                        if len(merged_results) >= 8:
                            break
                
                merged_results.extend(current_results)
                
                # 중복 제거 (더 관대한 기준)
                seen_keys = set()
                unique_results = []
                for r in merged_results:
                    title = r.get('title', '')[:50]
                    text = r.get('text', '')[:50]  # 100 → 50으로 변경 (더 많은 유사 문서 허용)
                    key = f"{title}|{text}"
                    
                    if key not in seen_keys:
                        seen_keys.add(key)
                        unique_results.append(r)
                
                ranked_results = unique_results[:12]  # 10 → 12개로 증가
                print(f"  ✅ 병합 완료: 고품질 {len(high_quality_docs[:5])}개 + 이전 {min(8-len(high_quality_docs[:5]), len(previous_results[:8]))}개 + 현재 {len(current_results)}개 → 총 {len(ranked_results)}개")
            
            # 4. 평가 시작 이벤트
            yield {
                "type": "status",
                "data": {
                    "status": "evaluating",
                    "message": "⚖️ 검색 결과를 평가하고 있습니다...",
                    "timestamp": time.time(),
                    "results_count": len(ranked_results)
                }
            }
            
            # 5. 충분성 평가
            is_sufficient = self._is_parallel_result_sufficient(ranked_results, decomposition or {})
            
            if is_sufficient:
                # 빠른 경로: 직접 답변
                print("✅ 병렬 검색 결과만으로 충분 - 직접 답변 생성")
                
                yield {
                    "type": "status",
                    "data": {
                        "status": "generating",
                        "message": "✍️ 답변을 생성하고 있습니다...",
                        "timestamp": time.time()
                    }
                }
                
                response = self._generate_direct_response(query, ranked_results, decomposition)
                
                yield {
                    "type": "result",
                    "data": response
                }
                
            else:
                # 느린 경로: Agent 추가 수집
                print("🔄 ReAct Agent로 추가 정보 수집")
                
                yield {
                    "type": "status",
                    "data": {
                        "status": "deep_search",
                        "message": "🧠 깊이 검색중... 정확한 답변을 찾고 있습니다",
                        "timestamp": time.time()
                    }
                }
                
                search_summary = self._format_parallel_results(ranked_results)
                
                if decomposition:
                    enhanced_query = f"""
{self._augment_query(query, decomposition)}

## 검색된 FDA 문서들
{search_summary}

위 정보를 활용하고, 부족한 부분만 추가 검색하세요.
정보 수집만 하고, 최종 답변은 생성하지 마세요.
"""
                else:
                    enhanced_query = f"""
{search_query}

## 검색된 FDA 문서들
{search_summary}

위 정보를 활용하고, 부족한 부분만 추가 검색하세요.
정보 수집만 하고, 최종 답변은 생성하지 마세요.
"""
                
                context = self.memory.get_context_for_agent()
                full_query = f"{context}\n{enhanced_query}" if context else enhanced_query
                
                # Agent로 정보 수집
                print("🔍 Agent 정보 수집 시작...")
                print("="*80)
                print("🤖 ReAct Agent 실행 시작 (상세 로그 활성화)")
                print("="*80)
                
                try:
                    handler = self.agent.run(user_msg=full_query)
                    collected_info = str(await handler)

                    print("="*80)
                    print("✅ ReAct Agent 실행 완료")
                    print(f"📝 수집된 정보 길이: {len(collected_info)}자")
                    print("="*80)
                except Exception as e:
                    print(f"❌ ReAct Agent 실행 중 오류: {e}")
                    import traceback
                    traceback.print_exc()
                    raise

                yield {
                    "type": "status",
                    "data": {
                        "status": "agent_complete",
                        "message": "✅ 추가 정보 수집 완료",
                        "timestamp": time.time()
                    }
                }
                
                # 최종 답변 생성
                yield {
                    "type": "status",
                    "data": {
                        "status": "generating",
                        "message": "✍️ 최종 답변을 생성하고 있습니다...",
                        "timestamp": time.time()
                    }
                }
                
                print("✅ 정보 수집 완료 - 최종 답변 생성")
                response = self._generate_response_with_agent_info(
                    query=query,
                    parallel_results=ranked_results,
                    agent_info=collected_info,
                    decomposition=decomposition
                )
                
                yield {
                    "type": "result",
                    "data": response
                }
            
            # 완료 이벤트
            yield {
                "type": "status",
                "data": {
                    "status": "completed",
                    "message": "✅ 답변 생성 완료",
                    "timestamp": time.time()
                }
            }
            
        except Exception as e:
            print(f"Error in chat_stream: {e}")
            yield {
                "type": "error",
                "data": {
                    "message": f"처리 중 오류가 발생했습니다: {str(e)}",
                    "timestamp": time.time()
                }
            }

