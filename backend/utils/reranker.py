# utils/reranker.py
"""
Cross-Encoder 기반 Reranking 시스템
하이브리드 검색 결과를 질문과의 정확한 관련성으로 재정렬
"""
from typing import List, Dict
import numpy as np
from sentence_transformers import CrossEncoder
import os


class CrossEncoderReranker:
    """
    Cross-Encoder를 사용하여 검색 결과 재정렬
    
    장점:
    - 질문과 문서 간 정확한 의미 매칭
    - Few-shot 예시 없이도 일반화 가능
    - 벡터 검색 + BM25의 약점 보완
    """
    
    def __init__(self, model_name: str = 'cross-encoder/ms-marco-MiniLM-L-6-v2'):
        """
        Args:
            model_name: HuggingFace Cross-Encoder 모델
                - ms-marco-MiniLM-L-6-v2: 빠르고 정확 (추천)
                - ms-marco-MiniLM-L-12-v2: 더 정확하지만 느림
        """
        print(f"🔧 Cross-Encoder Reranker 초기화 중: {model_name}")
        
        # 캐시 디렉토리 설정 (프로젝트 내부)
        cache_dir = os.path.join(os.path.dirname(__file__), '..', '.cache', 'sentence_transformers')
        os.makedirs(cache_dir, exist_ok=True)
        
        self.model = CrossEncoder(model_name, max_length=512, device='cpu')
        self.model_name = model_name
        print(f"✅ Reranker 초기화 완료")
    
    def rerank(
        self, 
        query: str, 
        results: List[Dict], 
        top_k: int = 10,
        score_field: str = 'hybrid_score'
    ) -> List[Dict]:
        """
        검색 결과를 Cross-Encoder로 재정렬
        
        Args:
            query: 사용자 질문
            results: 하이브리드 검색 결과 리스트
            top_k: 최종 반환할 문서 개수
            score_field: 기존 점수 필드명
        
        Returns:
            Reranking 점수로 정렬된 결과 (상위 top_k개)
        """
        if not results:
            return []
        
        print(f"\n🔍 Reranking 시작: {len(results)}개 문서")
        
        # 질문-문서 쌍 생성
        pairs = []
        for r in results:
            # 제목 + 내용 조합 (더 나은 매칭을 위해)
            title = r.get('title', '')
            text = r.get('text', '')
            
            # 제목이 있으면 제목 포함
            if title and title != 'N/A':
                doc_text = f"{title}\n\n{text[:1500]}"  # 제목 + 본문 1500자
            else:
                doc_text = text[:2000]  # 본문만 2000자
            
            pairs.append([query, doc_text])
        
        # Cross-Encoder로 점수 계산
        rerank_scores = self.model.predict(pairs)
        
        # 결과에 rerank 점수 추가
        for i, r in enumerate(results):
            r['rerank_score'] = float(rerank_scores[i])
            r['original_score'] = r.get(score_field, 0)  # 기존 점수 보존
        
        # Rerank 점수로 정렬
        reranked = sorted(results, key=lambda x: x['rerank_score'], reverse=True)
        
        # 상위 top_k개 선택
        top_results = reranked[:top_k]
        
        # 디버깅 정보
        print(f"📊 Reranking 결과:")
        for i, r in enumerate(top_results[:5], 1):
            original = r['original_score']
            rerank = r['rerank_score']
            collection = r.get('collection', 'unknown')
            title = r.get('title', 'N/A')[:50]
            
            print(f"  [{i}] {collection} - {title}")
            print(f"      원본: {original:.3f} → Rerank: {rerank:.3f}")
        
        return top_results
    
    def rerank_with_threshold(
        self, 
        query: str, 
        results: List[Dict], 
        min_score: float = 0.0,
        top_k: int = 10
    ) -> List[Dict]:
        """
        임계값 필터링을 포함한 reranking
        
        Args:
            query: 사용자 질문
            results: 검색 결과
            min_score: 최소 rerank 점수 (이하 제거)
            top_k: 최대 반환 개수
        
        Returns:
            필터링 + 정렬된 결과
        """
        reranked = self.rerank(query, results, top_k=len(results))
        
        # 임계값 필터링
        filtered = [r for r in reranked if r['rerank_score'] >= min_score]
        
        print(f"🔍 임계값 필터링: {len(reranked)}개 → {len(filtered)}개 (최소: {min_score})")
        
        return filtered[:top_k]
    
    def debug_scores(self, results: List[Dict], top_n: int = 10):
        """
        Reranking 점수 상세 분석 (디버깅용)
        """
        print(f"\n{'='*80}")
        print(f"🔬 Reranking 점수 상세 분석 (상위 {top_n}개)")
        print(f"{'='*80}\n")
        
        for i, r in enumerate(results[:top_n], 1):
            collection = r.get('collection', 'N/A')
            title = r.get('title', 'N/A')[:60]
            original = r.get('original_score', 0)
            rerank = r.get('rerank_score', 0)
            
            # 점수 변화 분석
            change = rerank - original
            change_symbol = "📈" if change > 0.1 else "📉" if change < -0.1 else "➡️"
            
            print(f"[{i:2d}] {collection:10s} | Rerank: {rerank:.4f} | Original: {original:.4f} | {change_symbol} {change:+.3f}")
            print(f"     제목: {title}")
            print()
        
        print(f"{'='*80}\n")


class RecencyBooster:
    """
    최신 문서 우선순위 부스터
    법/규정이 변경될 수 있는 주제는 최신 문서가 더 정확함
    """
    
    RECENCY_KEYWORDS = {
        'allergen': ['major food allergen', 'allergen list', 'sesame', 'faster act', 'nine allergen'],
        'regulation': ['cfr', 'u.s.c', 'section', 'regulation'],
        'year_indicators': ['2021', '2022', '2023', '2024', '2025'],  # 최신 연도
        'sesame_indicators': ['sesame', 'nine allergen', 'ninth allergen', 'faster act']  # 참깨 = 최신
    }
    
    @classmethod
    def is_recency_sensitive_question(cls, query: str) -> bool:
        """최신성이 중요한 질문인지 판별"""
        patterns = [
            'major food allergen', 'allergen list', 'what is', 'what are',
            'current', 'latest', 'new', 'updated'
        ]
        return any(p in query.lower() for p in patterns)
    
    @classmethod
    def boost_recent_documents(cls, results: List[Dict], boost_factor: float = 0.5) -> List[Dict]:
        """
        최신 정보 문서의 점수 상승
        
        특히 알레르겐 질문에서:
        - "sesame" 포함 = 2021년 FASTER Act 이후 최신 정보 (강력 부스팅)
        - 최신 연도 언급 = 최근 업데이트 정보
        
        Args:
            results: Reranked 결과
            boost_factor: 부스트 계수 (0.5 = 50% 상승)
        
        Returns:
            부스트된 결과
        """
        for r in results:
            text_lower = r.get('text', '').lower()
            title_lower = r.get('title', '').lower()
            combined = text_lower + ' ' + title_lower
            
            boost = 0.0
            reasons = []
            
            # 🔥 참깨 언급 = 최신 정보 (매우 강력한 지표!)
            if any(kw in combined for kw in cls.RECENCY_KEYWORDS['sesame_indicators']):
                boost += boost_factor * 2.0  # 2배 부스트!
                reasons.append("sesame/9개")
            
            # 최신 연도 언급 확인
            recent_years = [y for y in cls.RECENCY_KEYWORDS['year_indicators'] if y in combined]
            if recent_years:
                latest_year = max(recent_years)
                year_boost = boost_factor * (int(latest_year) - 2020) / 5
                boost += year_boost
                reasons.append(f"{latest_year}년")
            
            # 부스트 적용
            if boost > 0:
                original_score = r.get('rerank_score', 0)
                r['rerank_score'] = min(original_score + boost, 10.0)  # 상한 증가
                r['recency_boost'] = boost
                r['recency_reasons'] = reasons
                
                print(f"  📅 최신 문서 부스트: {r.get('title', '')[:40]}... ({', '.join(reasons)}) (+{boost:.3f})")
        
        return sorted(results, key=lambda x: x.get('rerank_score', 0), reverse=True)


class AuthorityQuestionBooster:
    """
    권한 질문 전용 부스터
    'cannot', 'Congress', 'statutory' 키워드 포함 문서 우선순위 상승
    """
    
    AUTHORITY_KEYWORDS = {
        'negative': ['cannot', 'unable to', 'prohibited', 'not authorized'],
        'congress': ['congress determines', 'congress decide', 'statutory', 'by law'],
        'legal_ref': ['section 201', 'section 403', 'fd&c act', 'u.s.c']
    }
    
    @classmethod
    def is_authority_question(cls, query: str) -> bool:
        """권한 질문인지 판별"""
        patterns = [
            'can fda', 'does fda have', 'who determines', 'who decides',
            'does congress', 'authority to', 'permitted to', 'allowed to'
        ]
        return any(p in query.lower() for p in patterns)
    
    @classmethod
    def boost_scores(cls, results: List[Dict], boost_factor: float = 0.15) -> List[Dict]:
        """
        권한 관련 키워드 포함 문서의 점수 상승
        
        Args:
            results: Reranked 결과
            boost_factor: 부스트 계수 (0.15 = 15% 상승)
        
        Returns:
            부스트된 결과
        """
        for r in results:
            text_lower = r.get('text', '').lower()
            
            # 키워드 매칭 카운트
            matches = 0
            matched_categories = []
            
            for category, keywords in cls.AUTHORITY_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in text_lower:
                        matches += 1
                        matched_categories.append(category)
                        break  # 카테고리당 1번만 카운트
            
            # 부스트 적용
            if matches > 0:
                boost = boost_factor * matches
                original_score = r.get('rerank_score', 0)
                r['rerank_score'] = min(original_score + boost, 1.0)  # 최대 1.0
                r['authority_boost'] = boost
                r['authority_keywords'] = list(set(matched_categories))
                
                print(f"  ⚡ 권한 키워드 부스트: {r.get('title', '')[:40]}... (+{boost:.3f})")
        
        # 부스트 후 재정렬
        return sorted(results, key=lambda x: x.get('rerank_score', 0), reverse=True)

