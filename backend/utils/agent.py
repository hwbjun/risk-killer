# utils/agent.py
"""
ReAct 프레임워크를 사용하여 FDA 규제 질문에 답변하는 메인 에이전트.
"""
import os
import json
import re
import time
from typing import List, Dict
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI
from llama_index.core import Settings
from llama_index.embeddings.openai import OpenAIEmbedding

from utils.tools import create_fda_tools
from utils.memory import ConversationMemory, ChatMessage
from utils.collection_strategy import COLLECTION_STRATEGY

class FDAAgent:
    def __init__(self):
        # LlamaIndex 전역 설정 (rag_engine과 동일하게 설정)
        Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small", api_key=os.getenv("OPENAI_API_KEY"))
        Settings.llm = OpenAI(model="gpt-4-turbo", temperature=0.1, api_key=os.getenv("OPENAI_API_KEY"))

        # 1. 모든 FDA 컬렉션을 '전문가 툴'로 변환
        self.fda_tools = create_fda_tools()

        # 멀티턴 대화를 위한 메모리 추가
        self.memory = ConversationMemory()
        
        # 제품 분해 캐시 추가
        self.decomposition_cache = {}

        # 컬렉션 라우팅 기본값 및 보조 LLM
        self.available_collections = ['guidance', 'ecfr', 'gras', 'dwpe', 'fsvp', 'rpm', 'usc']
        self.default_collections = ['guidance', 'ecfr', 'gras', 'dwpe']
        self.collection_classifier_llm = OpenAI(model="gpt-3.5-turbo", temperature=0)

        # ✅ [수정] 에이전트의 행동 방식을 정의하는 새로운 시스템 프롬프트 (정보 수집 전용)
        system_prompt = """당신은 FDA 규제 정보 수집 전문가입니다.

## 역할
사용자 질문에 답하기 위해 필요한 정보를 도구로 수집하세요.
최종 답변은 생성하지 마세요. 정보만 수집하세요.

## 수집해야 할 정보
1. CFR 규정 (구체적 번호 + 내용)
2. Import Alert 확인
3. 라벨링 요구사항
4. FSVP/검증 절차
5. 기타 관련 규제 정보

## 출력 형식
수집한 정보를 구조화된 형식으로 정리하세요:

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

## 도구 사용 강제 케이스
다음 키워드 포함 시 무조건 도구 사용:
- "비용", "cost", "payment", "supervision", "누가"
- "절차", "procedure", "process", "어떻게"
- "Chapter", "Section", "GRN", "CFR", "USC"
- "규정", "regulation", "requirement"
- "relabeling", "detention", "import", "GRAS"

**도구 회피 금지:**
❌ "(Implicit) I can answer without tools" → 절대 금지
❌ "일반적으로 알려진 바로는..." → 금지
✅ 반드시: Action → Observation → Answer 순서

## 최상위 규칙 (Golden Rule)
- 절대 사전 지식만으로 답변하지 마세요. 반드시 도구를 사용하여 검색하세요.
- **한국어 쿼리는 반드시 영어로 변환하여 도구에 전달하세요.**
- **도구를 선택하기 전에 쿼리를 분석하세요.**

## 쿼리 분석 절차 (도구 선택 전 필수!)

**Step 1: 쿼리 언어 확인**
- 한국어 있음 → 영어로 변환 필요
- 영어만 있음 → 그대로 사용

**Step 2: 키워드 기반 도구 판별**
- "GRN", "GRAS", "물질", "첨가물" → **gras/gras_approved/gras_withdrawn**
- "CFR", "21 CFR", "규정" → **ecfr**
- "Import Alert", "Red List", "수입 거부" → **dwpe**
- "Chapter", "Section", "RPM", "절차", "procedure" → **rpm**
- "21 USC", "법률", "처벌" → **usc**
- "FSVP", "수입자", "검증" → **fsvp**
- "Guidance", "라벨링" → **guidance**

## 검색 쿼리 작성 (영어 변환 필수!)

### RPM 한영 변환
- "개인용 수입" → "personal use importation"
- "절차" → "procedures process"
- "검사 거부" → "refusal entry detention"
- "relabeling 비용" → "relabeling supervision costs payment"
- "누가 내?" → "who pays costs responsibility"

### GRAS 한영 변환
- "대두" → "soy soybean"
- "음료" → "beverage drink water"

### eCFR 한영 변환
- "냉동식품" → "frozen food"
- "HACCP" → "HACCP hazard analysis"

### DWPE 한영 변환 + 동의어
- "해산물" → "fish fishery seafood shellfish aquatic marine"
- "중국" → "China Chinese"

### USC 한영 변환
- "부정표시" → "misbranding false labeling"
- "처벌" → "penalties violations"

## 재시도 전략
첫 검색 실패 시 2-3번 재시도 필수
"""

        # 2. ReAct 에이전트 생성 (context 추가)
        self.agent = ReActAgent.from_tools(
            tools=self.fda_tools,
            llm=Settings.llm,
            system_prompt=system_prompt,
            max_iterations=10,
            verbose=True,
            # ✅ 핵심 추가: context로 도구 강제 사용
            context="""You MUST use tools for FDA-related queries.
NEVER answer with "(Implicit) I can answer without tools".
For keywords like "비용/cost", "절차/procedure", "Chapter", "relabeling" → ALWAYS use tools.
Always translate Korean to English before searching."""
        )

    def _is_food_export_question_llm(self, query: str) -> bool:
        """
        빠르고 저렴한 LLM(gpt-3.5-turbo)을 사용하여 사용자의 질문이
        '특정 식품의 수출 규제'에 대한 것인지 분류하는 필터 함수.
        """
        try:
            # 필터 전용으로 저렴한 모델을 임시로 사용
            filter_llm = OpenAI(model="gpt-3.5-turbo", temperature=0)
            
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
        """일반 질문에 대한 LLM 쿼리 증강"""
        prompt = f"""
다음 사용자 질문을 FDA 규제 데이터베이스 검색에 최적화된 영어 쿼리로 변환하고 확장하세요.

사용자 질문: {original_query}

다음 요소들을 포함하여 검색 쿼리를 생성하세요:
1. 핵심 키워드를 영어로 변환
2. 관련 동의어 및 전문 용어 추가
3. FDA 규제 맥락에 맞는 검색어 확장
4. 컬렉션별 특화 키워드 포함

예시:
- "비용이 얼마나 드나요?" → "costs payment fees supervision relabeling expenses"
- "어떤 절차가 필요한가요?" → "procedures process requirements steps documentation"
- "규정은 무엇인가요?" → "regulations requirements CFR guidelines compliance"

변환된 검색 쿼리만 반환하세요 (설명 없이):
"""
        
        try:
            response = Settings.llm.complete(prompt)
            augmented_query = response.text.strip()
            
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

    def _format_parallel_results(self, results: List[Dict]) -> str:
        """병렬 검색 결과를 텍스트로 포맷"""
        if not results:
            return "병렬 검색 결과 없음"
        
        formatted = []
        for i, result in enumerate(results[:10], 1):  # 5 → 10개로 증가
            formatted.append(f"""
{i}. [{result['score']:.2f}] {result['collection']} {result.get('collection_role', '')}
   제목: {result.get('title', 'N/A')}
   내용: {result.get('text', 'N/A')[:800]}...  # 200 → 800자로 증가
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
            
            # orchestrator에 전달 (순수 검색만 담당)
            from utils.orchestrator import SimpleOrchestrator
            orchestrator = SimpleOrchestrator()
            
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
                agent_response = self.agent.chat(full_query)
                collected_info = str(agent_response)
                
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
- Include "ecfr" when regulations or CFR numbers are needed.
- Include "dwpe" for import alert or detention topics.
- Include "fsvp" for importer responsibilities or verification.
- Include "usc" for legal authority or penalties.
- Include "gras" for ingredient safety or additive status.
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
        for i, r in enumerate(results[:10], 1):
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
                "score": r['score']
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
            f"내용: {r.get('text', '')[:5000]}"
            for i, r in enumerate(results[:10])
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

❗️핵심 규칙:
- 중요한 정보나 규정을 언급할 때마다 해당하는 출처 번호를 [1], [2] 형태로 문장 끝에 삽입하세요.
- 여러 출처를 참고한 경우 [1][2] 처럼 연속으로 표시하세요.
- 반드시 [출처 N] 정보를 확인하고 정확한 번호를 사용하세요.

예시:
- 새우는 주요 알레르기 유발 물질로 표시해야 합니다[1].
- 21 CFR 1250.26과 Import Alert 16-50을 준수해야 합니다[2][3].

한국어로 구체적이고 실용적인 답변을 제공하세요.
"""
        else:
            prompt = f"""
사용자 질문: {query}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{full_context}

## 출처 목록
{source_list}

위 문서들을 종합하여 답변하되, 다음 규칙을 **반드시** 따르세요:

**내용 규칙:**
1. 질문에 직접 답변하세요
2. **문서에 리스트나 항목 나열이 있으면 반드시 모두 포함하세요**
   - 예: "A, B, C, D를 포함합니다" 또는 bullet point로 나열
   - 누락 금지: 모든 항목을 빠짐없이 기재
3. 구체적 수치, 이름, 규정 번호를 포함하세요
4. 일반론이 아닌 구체적 내용을 제공하세요

**Citation 규칙:**
- 각 주장 뒤에 [1], [2] 형식으로 출처 번호 표시
- 여러 출처는 [1][2] 처럼 연속으로 표시

**답변 예시:**
Q: What is X?
A: X는 ...을 의미합니다[1]. 여기에는 A, B, C, D, E가 포함됩니다[1][2]. 
   이는 ...하기 위함입니다[2].

한국어로 명확하고 구체적인 답변을 제공하세요.
"""
        
        response = Settings.llm.complete(prompt)
        
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
        for i, r in enumerate(parallel_results[:10], 1):
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
                "score": r['score']
            })
        
        # 출처 리스트 (프롬프트용)
        source_list = "\n".join([
            f"[출처 {c['index']}] {c['collection']}: {c['title'][:80]}"
            for c in citations
        ])
        
        # 병렬 검색 결과 정리 (Streamlit 스타일)
        parallel_context = "\n\n".join([
            f"[출처 {i+1}] {r['collection'].upper()} (점수: {r['score']:.3f})\n"
            f"제목: {r.get('title', 'N/A')}\n"
            f"내용: {r.get('text', '')[:5000]}"
            for i, r in enumerate(parallel_results[:10])
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
{agent_info}

## 출처 목록
{source_list}

위 모든 정보를 종합하여 다음을 포함한 답변을 작성하세요:
1. 구체적인 CFR 규정 번호와 내용
2. Import Alert 여부
3. 알레르기 라벨링 구체적 요구사항
4. FSVP 검증 절차
5. 실무 체크리스트 (5개 이상)

❗️핵심 규칙:
- 중요한 정보나 규정을 언급할 때마다 해당하는 출처 번호를 [1], [2] 형태로 문장 끝에 삽입하세요.
- 여러 출처를 참고한 경우 [1][2] 처럼 연속으로 표시하세요.
- 반드시 [출처 N] 정보를 확인하고 정확한 번호를 사용하세요.

예시:
- 새우는 주요 알레르기 유발 물질로 표시해야 합니다[1].
- 21 CFR 1250.26과 Import Alert 16-50을 준수해야 합니다[2][3].

한국어로 500단어 이상, 구체적이고 실용적인 답변을 제공하세요.
"""
        else:
            prompt = f"""
사용자 질문: {query}

📖 문서 컨텍스트 (각 내용 앞의 [출처 N]을 보고 주석을 달아야 함):
{parallel_context}

Agent가 추가 수집한 정보:
{agent_info}

## 출처 목록
{source_list}

위 모든 정보를 종합하여 답변하되, 다음 규칙을 **반드시** 따르세요:

**내용 규칙:**
1. 질문에 직접 답변하세요
2. **문서에 리스트나 항목 나열이 있으면 반드시 모두 포함하세요**
   - 예: "A, B, C, D를 포함합니다" 또는 bullet point로 나열
   - 누락 금지: 모든 항목을 빠짐없이 기재
3. 구체적 수치, 이름, 규정 번호를 포함하세요
4. 일반론이 아닌 구체적 내용을 제공하세요

**Citation 규칙:**
- 각 주장 뒤에 [1], [2] 형식으로 출처 번호 표시
- 여러 출처는 [1][2] 처럼 연속으로 표시

**답변 예시:**
Q: What is X?
A: X는 ...을 의미합니다[1]. 여기에는 A, B, C, D, E가 포함됩니다[1][2]. 
   이는 ...하기 위함입니다[2].

한국어로 명확하고 구체적인 답변을 제공하세요.
"""
        
        print(f"\n🤖 LLM 호출 중... (프롬프트: {len(prompt)}자)")
        
        # 단일 LLM 호출로 최종 답변 생성
        response = Settings.llm.complete(prompt)
        
        print(f"\n✅ 최종 답변 생성 완료!")
        print(f"  - 답변 길이: {len(response.text)}자")
        print(f"  - 답변 단어 수: {len(response.text.split())}단어")
        
        print(f"\n📋 Citations 생성 완료:")
        print(f"  - 총 {len(citations)}개 citations 생성")
        for c in citations:
            print(f"    [{c['index']}] {c['collection']}: {c['title'][:50]}...")
        
        # 최종 답변 내용 출력
        print("\n" + "="*60)
        print("📄 최종 답변 내용:")
        print("="*60)
        print(response.text)
        print("="*60 + "\n")
        
        return {
            "content": response.text,
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
        # 에이전트도 새로 시작
        self.agent.reset()


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
            from utils.orchestrator import SimpleOrchestrator
            orchestrator = SimpleOrchestrator()
            
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
                agent_response = self.agent.chat(full_query)
                collected_info = str(agent_response)
                
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
