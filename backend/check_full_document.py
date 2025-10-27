# check_full_document.py
import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from llama_index.embeddings.openai import OpenAIEmbedding

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

embed_model = OpenAIEmbedding(
    model="text-embedding-3-small",
    api_key=os.getenv("OPENAI_API_KEY")
)

query = "Can FDA add other allergens to the current list"
query_vector = embed_model.get_text_embedding(query)

results = client.search(
    collection_name="guidance",
    query_vector=query_vector,
    limit=10
)

print("="*80)
print("📄 전체 문서 내용 확인")
print("="*80)

for i, result in enumerate(results, 1):
    if result.score > 0.60:  # 0.6 이상만
        print(f"\n[{i}] 점수: {result.score:.3f}")
        print(f"제목: {result.payload.get('title', 'N/A')}")
        
        # 전체 텍스트 출력 (잘리지 않음)
        full_text = result.payload.get('text', result.payload.get('content', ''))
        print(f"\n전체 내용:\n{full_text}")
        print("\n" + "="*80)
        
        # "Congress" 키워드 체크
        if 'congress' in full_text.lower():
            print("✅ 'Congress' 키워드 발견!")
            # Congress 주변 텍스트 추출
            idx = full_text.lower().find('congress')
            snippet = full_text[max(0, idx-200):idx+200]
            print(f"관련 부분: ...{snippet}...")
        
        # "cannot" 키워드 체크  
        if 'cannot' in full_text.lower() or 'can not' in full_text.lower():
            print("✅ 'cannot' 키워드 발견!")