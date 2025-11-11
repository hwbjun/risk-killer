# utils/elasticsearch_client.py
"""
Elasticsearch 기반 BM25 검색 시스템
Elasticsearch의 내장 BM25 알고리즘 사용
"""
import os
import socket
import time
from typing import List, Dict, Optional
from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()


def _get_elasticsearch_host():
    """
    Elasticsearch 호스트 주소 자동 감지
    
    우선순위:
    1. ELASTICSEARCH_HOST 환경 변수
    2. Docker 환경 감지 (elasticsearch 호스트명 해석 가능 여부)
    3. 기본값: localhost (호스트에서 실행 시)
    """
    # 환경 변수가 명시적으로 설정되어 있으면 사용
    env_host = os.getenv("ELASTICSEARCH_HOST")
    if env_host:
        return env_host
    
    # Docker 네트워크 내부인지 확인 (elasticsearch 호스트명 해석 시도)
    try:
        socket.gethostbyname("elasticsearch")
        # 해석 성공 = Docker 네트워크 내부
        return "http://elasticsearch:9200"
    except socket.gaierror:
        # 해석 실패 = 호스트에서 실행 중
        return "http://localhost:9200"


class ElasticsearchService:
    """Elasticsearch BM25 검색 서비스"""
    
    def __init__(self, max_retries: int = 30, retry_delay: int = 2):
        """
        Elasticsearch 클라이언트 초기화
        
        Args:
            max_retries: 최대 재시도 횟수 (기본값: 30, 총 60초 대기)
            retry_delay: 재시도 간격 (초, 기본값: 2)
        """
        es_host = _get_elasticsearch_host()
        print(f"[ES] Connecting to Elasticsearch at {es_host}")
        
        self.client = Elasticsearch(
            [es_host],
            basic_auth=(
                os.getenv("ELASTICSEARCH_USER", "elastic"),
                os.getenv("ELASTICSEARCH_PASSWORD", "changeme123")
            ),
            # 연결 타임아웃 설정
            request_timeout=30
        )
        
        # 재시도 로직으로 연결 확인
        for attempt in range(max_retries):
            try:
                info = self.client.info()
                print(f"[ES] Connected to Elasticsearch {info['version']['number']}")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[ES] Connection attempt {attempt + 1}/{max_retries} failed: {e}")
                    print(f"[ES] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print(f"[ES] Connection error after {max_retries} attempts: {e}")
                    raise
    
    def search(
        self, 
        index_name: str, 
        query: str, 
        limit: int = 5,
        min_score: float = 0.0
    ) -> List[Dict]:
        """
        단일 인덱스에서 BM25 검색
        
        Args:
            index_name: 검색할 인덱스 이름
            query: 검색 쿼리
            limit: 최대 결과 개수
            min_score: 최소 점수 (이하 필터링)
        
        Returns:
            List[Dict]: [
                {
                    'score': float,
                    'text': str,
                    'title': str,
                    'url': str,
                    'collection': str
                }
            ]
        """
        try:
            # Elasticsearch match query (BM25 자동 사용)
            response = self.client.search(
                index=index_name,
                body={
                    "query": {
                        "match": {
                            "text": {
                                "query": query,
                                "operator": "or"
                            }
                        }
                    },
                    "size": limit,
                    "min_score": min_score
                }
            )
            
            # 결과 포맷팅 (Qdrant와 동일한 구조)
            results = []
            for hit in response['hits']['hits']:
                results.append({
                    'score': hit['_score'],
                    'text': hit['_source'].get('text', ''),
                    'title': hit['_source'].get('title', 'N/A'),
                    'url': hit['_source'].get('url', ''),
                    'collection': hit['_source'].get('collection', index_name),
                    'payload': hit['_source']
                })
            
            return results
            
        except Exception as e:
            print(f"[ES] Search error in {index_name}: {e}")
            return []
    
    def search_phrase(
        self,
        index_name: str,
        query: str,
        limit: int = 5
    ) -> List[Dict]:
        """
        정확한 구문 검색 (match_phrase)
        "21 CFR 117.3" 같은 정확한 매칭에 유용
        
        Args:
            index_name: 검색할 인덱스
            query: 검색 쿼리
            limit: 최대 결과 개수
        
        Returns:
            검색 결과 리스트
        """
        try:
            response = self.client.search(
                index=index_name,
                body={
                    "query": {
                        "match_phrase": {
                            "text": query
                        }
                    },
                    "size": limit
                }
            )
            
            results = []
            for hit in response['hits']['hits']:
                results.append({
                    'score': hit['_score'],
                    'text': hit['_source'].get('text', ''),
                    'title': hit['_source'].get('title', 'N/A'),
                    'url': hit['_source'].get('url', ''),
                    'collection': hit['_source'].get('collection', index_name),
                    'payload': hit['_source']
                })
            
            return results
            
        except Exception as e:
            print(f"[ES] Phrase search error in {index_name}: {e}")
            return []
    
    def multi_search(
        self,
        indices: List[str],
        query: str,
        limit_per_index: int = 5,
        min_score: float = 0.0
    ) -> Dict[str, List[Dict]]:
        """
        여러 인덱스에서 동시 검색
        
        Args:
            indices: 검색할 인덱스 리스트
            query: 검색 쿼리
            limit_per_index: 인덱스당 최대 결과 개수
            min_score: 최소 점수
        
        Returns:
            Dict[str, List[Dict]]: {
                'ecfr': [...],
                'gras': [...],
                ...
            }
        """
        results = {}
        
        for index_name in indices:
            results[index_name] = self.search(
                index_name=index_name,
                query=query,
                limit=limit_per_index,
                min_score=min_score
            )
        
        return results
    
    def search_all_collections(
        self,
        query: str,
        limit: int = 20,
        collections: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        모든 컬렉션에서 검색하고 통합된 결과 반환
        
        Args:
            query: 검색 쿼리
            limit: 최대 결과 개수
            collections: 검색할 컬렉션 목록 (None이면 전체)
        
        Returns:
            점수순으로 정렬된 통합 결과
        """
        if collections is None:
            collections = ['ecfr', 'fsvp', 'guidance', 'gras', 'dwpe', 'usc', 'rpm']
        
        all_results = []
        
        for collection in collections:
            results = self.search(
                index_name=collection,
                query=query,
                limit=limit
            )
            all_results.extend(results)
        
        # 점수순 정렬
        all_results.sort(key=lambda x: x['score'], reverse=True)
        
        return all_results[:limit]
    
    def check_index_exists(self, index_name: str) -> bool:
        """인덱스 존재 여부 확인"""
        try:
            return self.client.indices.exists(index=index_name)
        except Exception as e:
            print(f"[ES] Error checking index {index_name}: {e}")
            return False
    
    def get_index_count(self, index_name: str) -> int:
        """인덱스의 문서 개수 확인"""
        try:
            if not self.check_index_exists(index_name):
                return 0
            
            result = self.client.count(index=index_name)
            return result['count']
        except Exception as e:
            print(f"[ES] Error counting {index_name}: {e}")
            return 0