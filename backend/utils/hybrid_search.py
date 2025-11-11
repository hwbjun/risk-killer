# utils/hybrid_search.py
"""
하이브리드 검색: 벡터(Qdrant) + BM25(Elasticsearch) 결합
"""
from typing import List, Dict
import numpy as np


class HybridSearchRanker:
    """
    벡터 검색(Qdrant)과 BM25 검색(Elasticsearch) 결과를 결합하는 랭커
    
    벡터 검색: 의미 기반 유사도
    BM25 검색: 키워드 매칭 및 빈도 기반
    """
    
    def __init__(self, vector_weight: float = 0.6, bm25_weight: float = 0.4):
        """
        Args:
            vector_weight: 벡터 검색 가중치 (기본 0.6)
            bm25_weight: BM25 검색 가중치 (기본 0.4)
        """
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight
        
        assert abs(vector_weight + bm25_weight - 1.0) < 0.001, \
            "가중치 합은 1.0이어야 합니다"
    
    def _normalize_scores(self, results: List[Dict], score_key: str = 'score') -> List[Dict]:
        """
        점수를 0~1로 정규화 (Min-Max Normalization)
        
        Args:
            results: 검색 결과 리스트
            score_key: 점수 필드명
        
        Returns:
            정규화된 결과
        """
        if not results:
            return []
        
        scores = [r[score_key] for r in results]
        min_score = min(scores)
        max_score = max(scores)
        
        # 모든 점수가 같은 경우
        if max_score - min_score < 0.001:
            return [{'normalized_score': 1.0, **r} for r in results]
        
        # Min-Max 정규화
        normalized = []
        for r in results:
            norm_score = (r[score_key] - min_score) / (max_score - min_score)
            normalized.append({
                'normalized_score': norm_score,
                **r
            })
        
        return normalized
    
    def merge_results(
        self,
        vector_results: List[Dict],
        bm25_results: List[Dict],
        unique_key: str = 'text'
    ) -> List[Dict]:
        """
        벡터(Qdrant)와 BM25(Elasticsearch) 검색 결과를 하이브리드 병합
        
        Args:
            vector_results: Qdrant 벡터 검색 결과
            bm25_results: Elasticsearch BM25 검색 결과
            unique_key: 중복 제거용 키 (기본 'text')
        
        Returns:
            병합된 결과 (하이브리드 점수로 재정렬)
        """
        # 각 결과 정규화
        normalized_vector = self._normalize_scores(vector_results, 'score')
        normalized_bm25 = self._normalize_scores(bm25_results, 'score')
        
        # 문서별 점수 맵 생성
        score_map = {}
        
        # 벡터 검색 결과 추가
        for result in normalized_vector:
            key = result.get(unique_key, '')
            if key:
                score_map[key] = {
                    'vector_score': result['normalized_score'],
                    'bm25_score': 0.0,
                    'data': result
                }
        
        # BM25 검색 결과 추가/업데이트
        for result in normalized_bm25:
            key = result.get(unique_key, '')
            if key:
                if key in score_map:
                    # 이미 벡터 검색에서 찾은 문서
                    score_map[key]['bm25_score'] = result['normalized_score']
                else:
                    # BM25에서만 찾은 문서
                    score_map[key] = {
                        'vector_score': 0.0,
                        'bm25_score': result['normalized_score'],
                        'data': result
                    }
        
        # 하이브리드 점수 계산
        hybrid_results = []
        for key, scores in score_map.items():
            hybrid_score = (
                self.vector_weight * scores['vector_score'] +
                self.bm25_weight * scores['bm25_score']
            )
            
            hybrid_results.append({
                'hybrid_score': hybrid_score,
                'vector_score': scores['vector_score'],
                'bm25_score': scores['bm25_score'],
                **scores['data']
            })
        
        # 하이브리드 점수로 재정렬
        hybrid_results.sort(key=lambda x: x['hybrid_score'], reverse=True)
        
        return hybrid_results
    
    def debug_scores(self, results: List[Dict], top_n: int = 5):
        """
        하이브리드 점수 디버깅 출력
        """
        print(f"\n🔬 하이브리드 점수 분석 (상위 {top_n}개)")
        print("="*80)
        
        for i, r in enumerate(results[:top_n], 1):
            title = r.get('title', 'N/A')[:50]
            hybrid = r.get('hybrid_score', 0)
            vector = r.get('vector_score', 0)
            bm25 = r.get('bm25_score', 0)
            
            print(f"[{i}] Hybrid: {hybrid:.3f} = "
                  f"({self.vector_weight}*{vector:.3f}) + "
                  f"({self.bm25_weight}*{bm25:.3f})")
            print(f"    제목: {title}")
        
        print("="*80 + "\n")