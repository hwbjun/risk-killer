"""
SSE 엔드포인트 테스트 스크립트

사용법:
python test_sse.py
"""
import requests
import json
from urllib.parse import quote

def test_sse_endpoint():
    """SSE 엔드포인트를 테스트합니다."""
    
    # 테스트 질문
    test_query = "김치를 미국으로 수출하려면 어떤 규정을 확인해야 하나요?"
    
    # URL 인코딩
    encoded_query = quote(test_query)
    
    # SSE 엔드포인트 URL
    url = f"http://localhost:8000/api/chat/stream?query={encoded_query}"
    
    print(f"SSE 테스트 시작: {test_query}")
    print(f"URL: {url}\n")
    print("-" * 60)
    
    try:
        # SSE 연결
        response = requests.get(url, stream=True)
        
        if response.status_code != 200:
            print(f"❌ 오류: HTTP {response.status_code}")
            print(response.text)
            return
        
        print("✅ SSE 연결 성공!")
        print("-" * 60)
        
        # 이벤트 스트림 읽기
        event_count = 0
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                
                # SSE 형식 파싱
                if decoded_line.startswith('event:'):
                    event_type = decoded_line.split('event:')[1].strip()
                    print(f"\n📡 이벤트 타입: {event_type}")
                    event_count += 1
                    
                elif decoded_line.startswith('data:'):
                    data_str = decoded_line.split('data:')[1].strip()
                    try:
                        data = json.loads(data_str)
                        
                        if event_type == 'status':
                            status = data.get('status', 'unknown')
                            message = data.get('message', '')
                            print(f"   상태: {status}")
                            print(f"   메시지: {message}")
                            
                        elif event_type == 'result':
                            content = data.get('content', '')[:200]  # 처음 200자만
                            citations_count = len(data.get('citations', []))
                            print(f"   답변 길이: {len(data.get('content', ''))}자")
                            print(f"   Citations: {citations_count}개")
                            print(f"   답변 미리보기: {content}...")
                            
                        elif event_type == 'error':
                            error_msg = data.get('message', 'Unknown error')
                            print(f"   ❌ 에러: {error_msg}")
                            
                    except json.JSONDecodeError as e:
                        print(f"   ⚠️ JSON 파싱 실패: {e}")
                        print(f"   원본 데이터: {data_str[:100]}...")
        
        print("\n" + "-" * 60)
        print(f"✅ 테스트 완료! 총 {event_count}개 이벤트 수신")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ 연결 오류: {e}")
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 중단됨")
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("=" * 60)
    print("SSE 스트리밍 엔드포인트 테스트")
    print("=" * 60)
    print()
    
    test_sse_endpoint()

