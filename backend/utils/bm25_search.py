# utils/bm25_search.py
"""
BM25 키워드 기반 검색 시스템
"""
import os
from typing import List, Dict, Tuple
from rank_bm25 import BM25Okapi
from qdrant_client import QdrantClient
from dotenv import load_dotenv
import numpy as np

load_dotenv()


class BM25SearchService:
    """BM25 기반 키워드 검색 서비스"""
    
    def __init__(self):
        self.client = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY")
        )
        
        # 컬렉션별 BM25 인덱스 캐싱
        self.bm25_indices = {}
        self.documents_cache = {}
        
    def _load_collection_documents(self, collection_name: str) -> List:
        """컬렉션의 모든 문서 로드 (캐싱)"""
        if collection_name in self.documents_cache:
            return self.documents_cache[collection_name]
        
        print(f"📚 [{collection_name}] 문서 로딩 중...")
        all_documents = []
        offset = None
        limit = 100
        
        while True:
            results = self.client.scroll(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            points, next_offset = results
            all_documents.extend(points)
            
            if next_offset is None:
                break
            offset = next_offset
        
        self.documents_cache[collection_name] = all_documents
        print(f"✅ [{collection_name}] {len(all_documents)}개 문서 로드 완료")
        return all_documents
    
    def _build_bm25_index(self, collection_name: str):
        """컬렉션의 BM25 인덱스 생성 (캐싱)"""
        if collection_name in self.bm25_indices:
            return self.bm25_indices[collection_name]
        
        documents = self._load_collection_documents(collection_name)
        corpus = [doc.payload.get('text', '') for doc in documents]
        tokenized_corpus = [doc.lower().split() for doc in corpus]
        
        print(f"🔨 [{collection_name}] BM25 인덱스 생성 중...")
        bm25 = BM25Okapi(tokenized_corpus)
        self.bm25_indices[collection_name] = bm25
        print(f"✅ [{collection_name}] BM25 인덱스 생성 완료")
        
        return bm25
    
    def search(self, collection_name: str, query: str, limit: int = 5) -> List[Dict]:
        """
        BM25 검색 수행
        
        Returns:
            List[Dict]: [
                {
                    'score': float,
                    'text': str,
                    'title': str,
                    'url': str,
                    'payload': dict
                }
            ]
        """
        # BM25 인덱스 가져오기 (또는 생성)
        bm25 = self._build_bm25_index(collection_name)
        documents = self.documents_cache[collection_name]
        
        # 쿼리 토큰화
        tokenized_query = query.lower().split()
        
        # BM25 점수 계산
        scores = bm25.get_scores(tokenized_query)
        
        # 상위 N개 인덱스
        top_indices = np.argsort(scores)[::-1][:limit]
        
        # 결과 포맷팅
        results = []
        for idx in top_indices:
            if scores[idx] > 0:  # 점수가 0보다 큰 것만
                doc = documents[idx]
                results.append({
                    'score': float(scores[idx]),
                    'text': doc.payload.get('text', ''),
                    'title': doc.payload.get('title', 'N/A'),
                    'url': doc.payload.get('url', ''),
                    'payload': doc.payload
                })
        
        return results
    
    def clear_cache(self):
        """캐시 초기화"""
        self.bm25_indices.clear()
        self.documents_cache.clear()
        print("🗑️ BM25 캐시 초기화 완료")