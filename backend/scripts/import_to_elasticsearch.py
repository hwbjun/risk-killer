# backend/scripts/import_to_elasticsearch.py
import os
import json
from elasticsearch import Elasticsearch
from elasticsearch.helpers import streaming_bulk
from dotenv import load_dotenv
from pathlib import Path
from tqdm import tqdm

load_dotenv()

es = Elasticsearch(
    [os.getenv("ELASTICSEARCH_HOST", "http://elasticsearch:9200")],
    basic_auth=(
        os.getenv("ELASTICSEARCH_USER", "elastic"),
        os.getenv("ELASTICSEARCH_PASSWORD", "changeme123")
    )
)

data_dir = Path(__file__).parent.parent / "data"
COLLECTIONS = ['ecfr', 'fsvp', 'guidance', 'gras', 'dwpe', 'usc', 'rpm']

def generate_actions(documents, index_name):
    for doc in documents:
        yield {
            "_index": index_name,
            "_id": doc.get('id'),
            "_source": {
                "text": doc.get('text', ''),
                "title": doc.get('title', ''),
                "url": doc.get('url', ''),
                "collection": doc.get('collection', index_name)
            }
        }

def import_collection(collection_name):
    json_file = data_dir / f"{collection_name}_export.json"
    
    if not json_file.exists():
        print(f"  ❌ File not found: {json_file}")
        return 0
    
    print(f"\n📤 Importing {collection_name}...")
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            documents = json.load(f)
        
        total = len(documents)
        print(f"  📊 Total documents: {total}")
        
        actions = generate_actions(documents, collection_name)
        success_count = 0
        error_count = 0
        
        with tqdm(total=total, desc=f"  Uploading {collection_name}", unit="docs") as pbar:
            for ok, result in streaming_bulk(es, actions, chunk_size=1000, raise_on_error=False):
                if ok:
                    success_count += 1
                else:
                    error_count += 1
                pbar.update(1)
        
        es.indices.refresh(index=collection_name)
        count_result = es.count(index=collection_name)
        indexed_count = count_result['count']
        
        print(f"  ✅ {collection_name}: {indexed_count}/{total} documents indexed")
        
        if error_count > 0:
            print(f"  ⚠️  Errors: {error_count}")
        
        return indexed_count
        
    except Exception as e:
        print(f"  ❌ Error importing {collection_name}: {e}")
        import traceback
        traceback.print_exc()
        return 0

def main():
    print("\n" + "="*60)
    print("📦 JSON to Elasticsearch Import")
    print("="*60)
    
    try:
        info = es.info()
        print(f"\n📡 Connected to Elasticsearch")
        print(f"   Cluster: {info['cluster_name']}")
        print(f"   Version: {info['version']['number']}")
    except Exception as e:
        print(f"\n❌ Failed to connect to Elasticsearch: {e}")
        return
    
    total_indexed = 0
    results = {}
    
    for collection in COLLECTIONS:
        count = import_collection(collection)
        results[collection] = count
        total_indexed += count
    
    print("\n" + "="*60)
    print("📊 Import Summary")
    print("="*60)
    for collection, count in results.items():
        print(f"  {collection:12s}: {count:5d} documents")
    print("-"*60)
    print(f"  {'TOTAL':12s}: {total_indexed:5d} documents")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()