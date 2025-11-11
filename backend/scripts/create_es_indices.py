# backend/scripts/create_es_indices.py
import os
from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()

# Elasticsearch 클라이언트 초기화
es = Elasticsearch(
    [os.getenv("ELASTICSEARCH_HOST", "http://elasticsearch:9200")],
    basic_auth=(
        os.getenv("ELASTICSEARCH_USER", "elastic"),
        os.getenv("ELASTICSEARCH_PASSWORD", "changeme123")
    )
)

# 인덱스 목록
INDICES = ['ecfr', 'fsvp', 'guidance', 'gras', 'dwpe', 'usc', 'rpm']

# 인덱스 매핑 설정
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "text": {
                "type": "text",
                "analyzer": "standard"
            },
            "title": {
                "type": "text",
                "fields": {
                    "keyword": {
                        "type": "keyword"
                    }
                }
            },
            "url": {
                "type": "keyword"
            },
            "collection": {
                "type": "keyword"
            }
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "index": {
            "max_result_window": 10000
        }
    }
}

def create_index(index_name):
    """인덱스 생성"""
    try:
        # 기존 인덱스가 있으면 삭제
        if es.indices.exists(index=index_name):
            print(f"  ⚠️  Deleting existing index: {index_name}")
            es.indices.delete(index=index_name)
        
        # 새 인덱스 생성
        es.indices.create(index=index_name, body=INDEX_MAPPING)
        print(f"  ✅ Created index: {index_name}")
        
    except Exception as e:
        print(f"  ❌ Error creating {index_name}: {e}")
        raise

def main():
    print("\n" + "="*60)
    print("🏗️  Elasticsearch Index Creation")
    print("="*60)
    
    # 연결 확인
    try:
        info = es.info()
        print(f"\n📡 Connected to Elasticsearch")
        print(f"   Cluster: {info['cluster_name']}")
        print(f"   Version: {info['version']['number']}\n")
    except Exception as e:
        print(f"\n❌ Failed to connect to Elasticsearch: {e}")
        return
    
    # 인덱스 생성
    for index_name in INDICES:
        create_index(index_name)
    
    print("\n" + "="*60)
    print(f"✅ All {len(INDICES)} indices created successfully")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()