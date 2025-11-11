# backend/scripts/export_from_qdrant.py
import os
import json
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Qdrant 클라이언트 초기화
client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

# 내보낼 컬렉션 목록
COLLECTIONS = ['ecfr', 'fsvp', 'guidance', 'gras', 'dwpe', 'usc', 'rpm']

# 저장 디렉토리 생성
data_dir = Path(__file__).parent.parent / "data"
data_dir.mkdir(exist_ok=True)

def export_collection(collection_name):
    """컬렉션의 모든 문서를 JSON으로 추출"""
    print(f"\n📥 Exporting {collection_name}...")
    
    documents = []
    offset = None
    batch_size = 100
    total_count = 0
    
    try:
        while True:
            # Qdrant에서 문서 가져오기 (scroll)
            results = client.scroll(
                collection_name=collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False  # 벡터는 필요 없음
            )
            
            points, next_offset = results
            
            if not points:
                break
            
            # 문서 변환
            for point in points:
                doc = {
                    'id': point.id,
                    'text': point.payload.get('text', ''),
                    'title': point.payload.get('title', ''),
                    'url': point.payload.get('url', ''),
                    'collection': collection_name
                }
                documents.append(doc)
            
            total_count += len(points)
            print(f"  Progress: {total_count} documents...", end='\r')
            
            if next_offset is None:
                break
            offset = next_offset
        
        # JSON 파일로 저장
        output_file = data_dir / f"{collection_name}_export.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ {collection_name}: {total_count} documents exported to {output_file}")
        return total_count
        
    except Exception as e:
        print(f"\n❌ Error exporting {collection_name}: {e}")
        return 0

def main():
    print("\n" + "="*60)
    print("📦 Qdrant to JSON Export")
    print("="*60)
    
    total_documents = 0
    
    for collection in COLLECTIONS:
        count = export_collection(collection)
        total_documents += count
    
    print("\n" + "="*60)
    print(f"✅ Export completed: {total_documents} total documents")
    print(f"📁 Files saved in: {data_dir}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()