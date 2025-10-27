# FDA RAG 시스템 평가 가이드

## 📁 파일 구조

```
backend/evaluation/
├── test_single.py          # 단일 테스트 (디버깅용)
├── run_evaluation.py       # 전체 평가 (성능 측정용)
├── evaluator.py            # 평가 로직
├── test_dataset.py         # 테스트 데이터셋
└── results/                # 평가 결과 저장
    └── baseline_20251024_*.json
```

---

## 🎯 두 가지 평가 도구

### 1. `test_single.py` - 빠른 디버깅 🔧

**목적**: 하나의 테스트 케이스를 상세하게 확인

**언제 사용**:
- ✅ 코드 수정 후 빠르게 확인할 때
- ✅ 특정 질문의 답변을 자세히 볼 때
- ✅ 검색 문서 내용을 확인할 때
- ✅ 디버깅할 때

**사용법**:
```bash
cd backend/evaluation

# 기본 (definition_001)
python test_single.py

# 특정 테스트 케이스
python test_single.py --id authority_001

# 실제 챗봇 모드 (temperature=0.1)
python test_single.py --real-chatbot --id definition_001
```

**출력 예시**:
```
================================================================================
[TEST] definition_001
================================================================================

[Q] 질문: What is a major food allergen?
[A] 정답: A major food allergen includes milk, eggs...
[K] 키워드: ['milk', 'eggs', 'fish', ...]

🤖 Agent 답변 생성 중...
🔍 검색된 문서 내용 (디버깅)
============================================================
[출처 1] GUIDANCE (점수: 0.710)
제목: Sec. 555.250  Major Food Allergen
📏 원본 텍스트 길이: 1998자
내용 (첫 1000자): ...

[Response] Agent 답변:
--------------------------------------------------------------------------------
A major food allergen, as defined under FALCPA...
--------------------------------------------------------------------------------

[Generation] 평가:
  - Correctness:       1.000 / 1.0
  - Faithfulness:      1.000 / 1.0
  - Keyword Coverage:  80.0%
```

---

### 2. `run_evaluation.py` - 전체 평가 📊

**목적**: 모든 테스트 케이스를 실행하고 통계 생성

**언제 사용**:
- ✅ 시스템 전체 성능을 측정할 때
- ✅ 버전 간 성능을 비교할 때
- ✅ 개선 효과를 정량적으로 확인할 때
- ✅ 리포트를 생성할 때

**사용법**:
```bash
cd backend/evaluation

# 기본 평가
python run_evaluation.py

# 버전명 지정
python run_evaluation.py --version v1.0_improved

# 실제 챗봇 모드
python run_evaluation.py --real-chatbot --version production_test
```

**출력 예시**:
```
================================================================================
🧪 FDA RAG 시스템 평가 - baseline
================================================================================

📝 테스트 케이스: 5개
카테고리: {'DEFINITION', 'PROCEDURE', 'AUTHORITY'}

================================================================================
[1/5] definition_001
================================================================================
❓ 질문: What is a major food allergen?
✅ 정답: A major food allergen includes milk, eggs...
🔑 키워드: ['milk', 'eggs', 'fish', ...]

🤖 Agent 답변 생성 중...

📝 답변 (첫 200자):
   A major food allergen, as defined under the Food Allergen Labeling...

📚 검색된 문서: 5개
   [1] guidance: Sec. 555.250  Major Food Allergen... (점수: 0.710)
   [2] guidance: Guidance for Industry: Food Labeling Guide... (점수: 0.706)
   ...

📊 평가 시작...

✅ 평가 완료:
   - Correctness:  1.00
   - Faithfulness: 1.00
   - Keyword:      80%

================================================================================
[2/5] procedure_001
================================================================================
...

================================================================================
🎯 평가 완료 - 최종 결과
================================================================================

📊 전체 평균 점수:
  ✅ Correctness (정확성):     0.950 / 1.0
  📝 Faithfulness (충실도):    0.920 / 1.0
  🎯 Relevancy (관련성):       0.980 / 1.0
  🔍 Similarity (유사도):      0.650 / 1.0
  🔑 Keyword Coverage (키워드): 75.0%

🏆 종합 평가: A+ (우수)

📂 카테고리별 상세:

  📌 DEFINITION (2개 테스트)
     - Correctness:  1.000
     - Faithfulness: 1.000

  📌 PROCEDURE (2개 테스트)
     - Correctness:  0.900
     - Faithfulness: 0.850

💾 상세 결과 저장: evaluation/results/baseline_20251024_163245.json
📁 파일 위치: backend/evaluation/results/
```

---

## 📊 평가 지표 설명

### 1. **Correctness (정확성)** - 가장 중요!
- **의미**: 정답과 얼마나 일치하는가?
- **범위**: 0.0 ~ 1.0 (1-5 스케일을 정규화)
- **목표**: 0.8 이상
- **예시**:
  - 1.0: 완벽한 답변
  - 0.75: 대부분 정확, 일부 누락
  - 0.5: 절반만 정확

### 2. **Faithfulness (충실도)** - 환각 방지!
- **의미**: 검색된 문서에 충실한가? (환각 없음)
- **범위**: 0.0 ~ 1.0
- **목표**: 0.9 이상
- **중요**: 0.0이면 환각 발생 (대참사!)
- **예시**:
  - 1.0: 모든 내용이 문서 기반
  - 0.5: 절반은 지어낸 내용
  - 0.0: 검색 문서 내용 없음 (심각)

### 3. **Relevancy (관련성)**
- **의미**: 질문과 관련된 답변인가?
- **범위**: 0.0 ~ 1.0
- **목표**: 0.9 이상

### 4. **Similarity (유사도)**
- **의미**: 표현이 정답과 비슷한가?
- **범위**: 0.0 ~ 1.0
- **참고**: 낮아도 OK (내용이 정확하면 됨)

### 5. **Keyword Coverage (키워드 커버리지)**
- **의미**: 중요 키워드를 얼마나 포함하는가?
- **범위**: 0% ~ 100%
- **목표**: 70% 이상

---

## 🎯 평가 등급 기준

### 종합 점수 계산
```python
평균 = (Correctness + Faithfulness + Relevancy) / 3
```

### 등급표
| 평균 점수 | 등급 | 의미 |
|---------|------|------|
| 0.9+ | **A+ (우수)** | 프로덕션 배포 가능 ✅ |
| 0.8 - 0.9 | **A (양호)** | 거의 완성 단계 |
| 0.7 - 0.8 | **B+ (보통)** | 개선 필요 |
| 0.7 미만 | **B (개선 필요)** | 추가 작업 필요 |

---

## 🔄 개발 워크플로우

### 1. 개발 중 (빠른 확인)
```bash
# 코드 수정
vim backend/utils/agent.py

# 단일 테스트로 확인
python evaluation/test_single.py --id definition_001

# 문제 없으면 다음 테스트
python evaluation/test_single.py --id procedure_001
```

### 2. 배포 전 (전체 평가)
```bash
# 전체 평가 실행
python evaluation/run_evaluation.py --version v1.2_final

# 결과 확인
cat evaluation/results/v1.2_final_*.json

# 이전 버전과 비교
```

### 3. 버전 비교
```bash
# baseline 평가
python run_evaluation.py --version baseline

# 개선 후 평가
python run_evaluation.py --version improved

# 두 JSON 파일 비교
```

---

## 📝 테스트 케이스 추가 방법

### `test_dataset.py` 수정

```python
def get_dataset():
    return [
        # ... 기존 테스트 ...
        
        # 새 테스트 추가
        {
            "id": "new_test_001",
            "category": "COMPLIANCE",
            "difficulty": "hard",
            "question": "What are the HACCP requirements for seafood?",
            "ground_truth": "Seafood HACCP requires...",
            "expected_keywords": ["HACCP", "seafood", "critical control points", "monitoring"],
            "expected_collections": ["ecfr", "guidance"]
        }
    ]
```

---

## 🐛 트러블슈팅

### 문제 1: Faithfulness가 0.0
```
원인: citations에 content 필드 없음
해결: agent.py에서 content 추가 확인
```

### 문제 2: 답변이 2개만 나열
```
원인: 검색 문서 부족 (QUOTA_PER_COLLECTION=2)
해결: orchestrator.py에서 5로 증가
```

### 문제 3: 매번 다른 결과
```
원인: temperature=0.1
해결: test_single.py에서 temperature=0 사용
```

---

## 📊 결과 파일 분석

### JSON 구조
```json
{
  "summary": {
    "total_tests": 5,
    "timestamp": "2025-10-24T16:32:45"
  },
  "overall_metrics": {
    "correctness": 0.950,
    "faithfulness": 0.920,
    "relevancy": 0.980,
    "similarity": 0.650,
    "keyword_coverage": 0.750
  },
  "by_category": {
    "DEFINITION": {
      "count": 2,
      "correctness": 1.000,
      "faithfulness": 1.000
    }
  },
  "detailed_results": [
    {
      "test_id": "definition_001",
      "question": "What is a major food allergen?",
      "generation": {
        "correctness": 1.0,
        "faithfulness": 1.0,
        ...
      }
    }
  ]
}
```

---

## 💡 모범 사례

### DO ✅
- 코드 수정 후 `test_single.py`로 빠른 확인
- 배포 전 `run_evaluation.py`로 전체 평가
- 결과를 JSON 파일로 저장하여 버전 관리
- 정기적으로 평가 실행 (주 1회)

### DON'T ❌
- 단일 테스트만 보고 배포하기
- Faithfulness 0.0을 무시하기
- 평가 없이 프롬프트 대폭 수정
- 실제 챗봇 모드로 평가하기 (일관성 떨어짐)

---

## 🎯 목표 성능 (프로덕션)

| 지표 | 목표 | 현재 (definition_001) |
|------|------|---------------------|
| Correctness | 0.9+ | ✅ 1.0 |
| Faithfulness | 0.9+ | ✅ 1.0 |
| Relevancy | 0.9+ | ✅ 1.0 |
| Similarity | 0.6+ | ✅ 0.66 |
| Keyword | 70%+ | ✅ 80% |

**현재 상태**: 프로덕션 배포 가능! 🚀

---

## 📚 추가 자료

- `README_평가차이분석.md` - 평가와 실제 챗봇 차이 설명
- `evaluator.py` - 평가 로직 구현
- `test_dataset.py` - 테스트 데이터셋

---

## ❓ FAQ

**Q: test_single.py와 run_evaluation.py 중 뭘 사용해야 하나요?**
A: 
- 개발 중: `test_single.py` (빠른 디버깅)
- 최종 확인: `run_evaluation.py` (전체 성능)

**Q: 둘 다 필요한가요?**
A: 네! 역할이 다릅니다.
- `test_single.py`: 상세한 디버깅
- `run_evaluation.py`: 통계적 성능 측정

**Q: 실제 챗봇 모드는 언제 사용하나요?**
A: 사용자 경험 테스트용입니다. 평가에는 기본 모드(deterministic)를 사용하세요.

**Q: Faithfulness가 왜 중요한가요?**
A: 환각(hallucination)을 방지합니다. 0.0이면 검색 문서 없이 답변한 것이므로 심각한 문제입니다.





