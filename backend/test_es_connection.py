import os
from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    print("\n🔍 Elasticsearch Connection Test")
    print("="*50)
    
    # 환경 변수에서 설정 읽기
    es_host = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
    es_user = os.getenv("ELASTICSEARCH_USER", "elastic")
    es_password = os.getenv("ELASTICSEARCH_PASSWORD", "changeme123")
    
    print(f"Host: {es_host}")
    print(f"User: {es_user}")
    
    # Elasticsearch 클라이언트 생성
    es = Elasticsearch(
        [es_host],
        basic_auth=(es_user, es_password)
    )
    
    try:
        # 1. 연결 확인
        info = es.info()
        print(f"✅ Connected to Elasticsearch")
        print(f"   Cluster: {info['cluster_name']}")
        print(f"   Version: {info['version']['number']}")
        
        # 2. 테스트 인덱스 생성
        test_index = "test_index"
        if es.indices.exists(index=test_index):
            es.indices.delete(index=test_index)
        
        es.indices.create(index=test_index)
        print(f"✅ Test index created: {test_index}")
        
        # 3. 테스트 인덱스 삭제
        es.indices.delete(index=test_index)
        print(f"✅ Test index deleted")
        
        print("="*50)
        print("✅ All tests passed!")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        raise

if __name__ == "__main__":
    test_connection()