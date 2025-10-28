# utils/orchestrator.py
import asyncio
from typing import List, Dict, Any, Optional
import time
from utils.qdrant_client import QdrantService
from utils.bm25_search import BM25SearchService  
from utils.hybrid_search import HybridSearchRanker
from utils.reranker import CrossEncoderReranker, AuthorityQuestionBooster, RecencyBooster
from concurrent.futures import ThreadPoolExecutor
import threading
from utils.collection_strategy import generate_optimized_query, smart_collection_selection, COLLECTION_STRATEGY


# ============================================================
# 🚀 Phase 1 최적화: 싱글톤 패턴으로 재사용
# ============================================================

class GlobalServices:
    """전역 서비스 싱글톤 - 한 번만 초기화"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def initialize(self):
        """서버 시작 시 한 번만 호출"""
        if self._initialized:
            return
        
        with self._lock:
            if not self._initialized:
                print("🔧 전역 서비스 초기화 중...")
                
                # Qdrant 연결 (한 번만)
                self.qdrant_service = QdrantService()
                print("✅ Qdrant 연결 완료")
                
                # BM25 서비스
                self.bm25_service = BM25SearchService()
                print("✅ BM25 서비스 로드 완료")
                
                # Reranker (한 번만 로드 - 가장 무거움)
                print("🔧 Cross-Encoder Reranker 초기화 중: cross-encoder/ms-marco-MiniLM-L-6-v2")
                self.reranker = CrossEncoderReranker(model_name='cross-encoder/ms-marco-MiniLM-L-6-v2')
                print("✅ Reranker 초기화 완료")
                
                # Hybrid Ranker
                self.hybrid_ranker = HybridSearchRanker(
                    vector_weight=0.5,
                    bm25_weight=0.5
                )
                
                self._initialized = True
                print("✅ 전역 서비스 초기화 완료!\n")
    
    @property
    def is_initialized(self):
        return self._initialized


# 전역 인스턴스 생성
_global_services = GlobalServices()


class SimpleOrchestrator:
    """순수 검색 전용 오케스트레이터 - 하이브리드 검색 지원 (Phase 1 최적화)"""
    
    def __init__(self):
        # 전역 서비스 초기화 (처음 한 번만 실행됨)
        _global_services.initialize()
        
        # 전역 서비스 참조 (복사 아님!)
        self.qdrant_service = _global_services.qdrant_service
        self.bm25_service = _global_services.bm25_service
        self.hybrid_ranker = _global_services.hybrid_ranker
        self.reranker = _global_services.reranker
        
        # 스레드 풀은 인스턴스별 (가볍기 때문)
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        print("✅ Orchestrator 초기화 완료 (전역 서비스 재사용)")
    
    def _search_collection_sync(self, collection: str, query: str, limit: int = 5):
        """동기식 검색 (스레드에서 실행용)"""
        try:
            # 새 이벤트 루프 생성 (각 스레드별로)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self.qdrant_service.search_collection(collection, query, limit)
                )
                return result
            finally:
                loop.close()
        except Exception as e:
            print(f"Error in {collection}: {e}")
            return []
    
    def parallel_search(self, query: str, collections: List[str], decomposition: dict = None) -> Dict[str, Any]:
        """
        하이브리드 검색: 벡터 + BM25 병합
        Phase 1 최적화: limit 조정 (5개 → 3개)
        """
        start_time = time.time()
        
        # 원본 쿼리 추출 (증강 제거)
        if "Enhanced search query:" in query:
            original_query = query.split("Enhanced search query:")[0].strip()
            augmented_query = query
        else:
            original_query = query
            augmented_query = query
        
        print(f"\n🔍 원본 쿼리: {original_query[:60]}...")
        print(f"✨ 증강 쿼리: {augmented_query[:60]}...")
        
        # 컬렉션별 최적화된 쿼리 생성
        optimized_queries = self._generate_optimized_queries(collections, decomposition, augmented_query)
        
        # 검색 limit (쿼리 변동성 대응)
        SEARCH_LIMIT = 5  # 컬렉션당 5개
        
        # 병렬 검색 실행
        futures = []
        for collection in collections:
            collection_query = optimized_queries.get(collection, augmented_query)
            future = self.executor.submit(
                self._search_collection_hybrid,
                collection,
                original_query,  # BM25용 (원본)
                collection_query,  # 벡터용 (증강)
                SEARCH_LIMIT  # 🚀 3개로 제한
            )
            futures.append((collection, future))
        
        # 결과 수집
        combined = {
            "search_time": time.time() - start_time,
            "results_by_collection": {}
        }
        
        print(f"\n📊 컬렉션별 하이브리드 검색 결과 (limit={SEARCH_LIMIT}):")
        for collection, future in futures:
            try:
                result = future.result(timeout=15)
                combined["results_by_collection"][collection] = result
                
                if result:
                    scores = [r['hybrid_score'] for r in result]
                    print(f"  {collection}: {len(result)}개 결과, 하이브리드 점수: {[f'{s:.3f}' for s in scores[:3]]}")
                else:
                    print(f"  {collection}: 0개 결과")
                    
            except Exception as e:
                print(f"Error getting result for {collection}: {e}")
                combined["results_by_collection"][collection] = []
        
        combined["search_time"] = time.time() - start_time
        return combined
    
    def _search_collection_hybrid(
            self, 
            collection: str, 
            original_query: str,
            augmented_query: str,
            limit: int = 5  # 기본값 5
        ) -> List[Dict]:
        """
        단일 컬렉션에 대해 벡터 + BM25 하이브리드 검색
        """
        try:
            # 1. 벡터 검색 (증강 쿼리)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                vector_results = loop.run_until_complete(
                    self.qdrant_service.search_collection(collection, augmented_query, limit)
                )
            finally:
                loop.close()
            
            # 벡터 결과 포맷팅
            vector_formatted = [
                {
                    'score': r.score,
                    'text': r.payload.get('text', ''),
                    'title': r.payload.get('title', 'N/A'),
                    'url': r.payload.get('url', '')
                }
                for r in vector_results
            ]
            
            # 2. BM25 검색 (원본 쿼리)
            bm25_results = self.bm25_service.search(collection, original_query, limit)
            
            # 3. 하이브리드 병합
            hybrid_results = self.hybrid_ranker.merge_results(
                vector_formatted,
                bm25_results,
                unique_key='text'
            )
            
            return hybrid_results
            
        except Exception as e:
            print(f"Hybrid search error in {collection}: {e}")
            return []
    
    def _generate_optimized_queries(self, collections: List[str], decomposition: dict = None, raw_query: str = None) -> dict:
        """컬렉션별 최적화된 쿼리 생성 (전략 문서 기반)"""
        queries = {}
        
        for collection in collections:
            if decomposition:
                # 제품 분해 기반 쿼리 (기존)
                queries[collection] = generate_optimized_query(collection, decomposition)
            elif raw_query:
                # 일반 질문: 증강된 쿼리 활용
                # "Enhanced search query:" 이후 텍스트만 추출
                if "Enhanced search query:" in raw_query:
                    augmented_part = raw_query.split("Enhanced search query:")[1].strip()
                else:
                    augmented_part = raw_query
                
                # 컬렉션 컨텍스트 추가
                strategy = COLLECTION_STRATEGY.get(collection, {})
                context = strategy.get('role', '')
                queries[collection] = f"{context}: {augmented_part}" if context else augmented_part
            else:
                # 폴백: 기본 쿼리
                queries[collection] = "food import export FDA requirements"
        
        return queries
    
    def merge_and_rank(self, parallel_results: dict, original_query: str = None) -> List[Dict]:
        """
        하이브리드 검색 결과를 병합하고 Reranking
        """
        MIN_SCORE = 0.20  # 하이브리드 점수 기준 (0.30 → 0.25 → 0.20으로 낮춤)
        QUOTA_PER_COLLECTION = 5  # 컬렉션당 5개
        
        final = []
        collection_stats = {}
        
        for collection, results in parallel_results['results_by_collection'].items():
            # hybrid_score 기준 필터링
            qualified = [r for r in results if r.get('hybrid_score', 0) >= MIN_SCORE]
            selected = qualified[:QUOTA_PER_COLLECTION]
            
            collection_info = COLLECTION_STRATEGY.get(collection, {})
            
            for item in selected:
                final.append({
                    "collection": collection,
                    "collection_role": collection_info.get('role', ''),
                    "collection_desc": collection_info.get('description', ''),
                    "score": item.get('hybrid_score', 0),
                    "hybrid_score": item.get('hybrid_score', 0),
                    "vector_score": item.get('vector_score', 0),
                    "bm25_score": item.get('bm25_score', 0),
                    "text": item.get('text', '')[:5000],  # 🚀 10000 → 5000자로 축소
                    "title": item.get('title', ''),
                    "url": item.get('url', '')
                })
            
            collection_stats[collection] = {
                'total': len(results),
                'qualified': len(qualified),
                'selected': len(selected),
                'scores': [r.get('hybrid_score', 0) for r in selected]
            }
        
        print(f"\n📊 하이브리드 랭킹 결과 (최소 점수: {MIN_SCORE}, 쿼터: {QUOTA_PER_COLLECTION})")
        for coll, stats in collection_stats.items():
            print(f"  {coll}: {stats['selected']}개 선발")
        print(f"📌 총 {len(final)}개, 컬렉션 {len(collection_stats)}개")
        
        # Reranking 적용 (전역 reranker 재사용)
        if original_query and final:
            print(f"\n🔄 Cross-Encoder Reranking 시작...")
            
            # Reranking top_k
            TOP_K = 15
            
            # Reranking 수행
            reranked = self.reranker.rerank(
                query=original_query,
                results=final,
                top_k=TOP_K,
                score_field='hybrid_score'
            )
            
            # 최신성 부스팅 먼저 적용 (알레르겐 리스트 등)
            if RecencyBooster.is_recency_sensitive_question(original_query):
                print(f"📅 최신성 중요 질문 감지 - 최신 문서 부스팅 적용")
                reranked = RecencyBooster.boost_recent_documents(reranked, boost_factor=0.2)
            
            # 권한 질문이면 키워드 부스팅 추가
            if AuthorityQuestionBooster.is_authority_question(original_query):
                print(f"⚡ 권한 질문 감지 - 키워드 부스팅 적용")
                reranked = AuthorityQuestionBooster.boost_scores(reranked, boost_factor=0.15)
            
            # score 필드를 rerank_score로 업데이트
            for r in reranked:
                r['score'] = r.get('rerank_score', r.get('score', 0))
            
            print(f"✅ Reranking 완료: 최종 {len(reranked)}개 (top_k={TOP_K})\n")
            return reranked
        else:
            # Reranking 없이 hybrid score로만 정렬
            return sorted(final, key=lambda x: x['score'], reverse=True)
    
    
    def determine_collections(self, decomposition: dict) -> List[str]:
        """순수 검색 기능: 제품 특성에 따른 컬렉션 선택"""
        return smart_collection_selection(decomposition)
    
    def __del__(self):
        """소멸자에서 스레드 풀 정리"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)


# ============================================================
# 🚀 서버 시작 시 호출할 초기화 함수
# ============================================================

def initialize_global_services():
    """
    main.py에서 서버 시작 시 한 번만 호출
    
    Usage in main.py:
        from utils.orchestrator import initialize_global_services
        
        @app.on_event("startup")
        async def startup_event():
            initialize_global_services()
    """
    _global_services.initialize()