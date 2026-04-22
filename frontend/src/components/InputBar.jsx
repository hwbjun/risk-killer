import React, { useEffect, useRef, useState } from 'react';

// 랜딩 페이지 입력창 placeholder 애니메이션용 예시 문구
const PLACEHOLDER_QUESTIONS = [
  "주요 식품 알레르기 유발물질은 무엇인가요?",
  "미국에 냉동식품 수출 시 준수해야 할 규정을 알려줘요.",
  "FDA가 알레르기 목록에 새 항목을 추가할 수 있나요?",
  "FDA 요구사항에 맞는 영양성분표는 어떻게 작성하나요?",
  "식품 알레르겐 표시 규정과 방법을 알려주세요."
];

const TYPE_SPEED_MS = 90;
const PAUSE_AFTER_TYPED_MS = 2500;
const PAUSE_BEFORE_NEXT_MS = 400;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const InputBar = ({ inputMessage, setInputMessage, isTyping, onSend, onKeyPress, onDrop, onDragOver, onDragLeave, dragOver, fileInputRef, onFileChange, isCentered = false }) => {
  // ─── 랜딩 페이지 placeholder 타이핑 애니메이션 ───
  // reduced-motion 사용자는 첫 문구 고정, 그 외에는 빈 값으로 시작하여 타이핑
  const [animatedPlaceholder, setAnimatedPlaceholder] = useState(() => {
    if (!isCentered) return '어떤 도움을 드릴까요?';
    if (prefersReducedMotion()) return PLACEHOLDER_QUESTIONS[0];
    return '';
  });
  const [questionIndex, setQuestionIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const pauseTimerRef = useRef(null);
  const nextTimerRef = useRef(null);

  useEffect(() => {
    // 랜딩 페이지가 아니거나, 사용자 포커스 시 또는 reduced-motion 설정 시 애니메이션 중단
    if (!isCentered) return;
    if (prefersReducedMotion()) {
      setAnimatedPlaceholder(PLACEHOLDER_QUESTIONS[0]);
      return;
    }
    if (isPaused) return;

    const currentQuestion = PLACEHOLDER_QUESTIONS[questionIndex];

    // 타이핑 완료 → 일시정지 후 전체 비우고 다음 문구로 (모든 state를 한 배치에 업데이트)
    if (charIndex === currentQuestion.length) {
      pauseTimerRef.current = setTimeout(() => {
        // 빈 상태로 400ms 보여주기 위해 먼저 placeholder만 비움
        setAnimatedPlaceholder('');
        nextTimerRef.current = setTimeout(() => {
          // 다음 문구 인덱스 + charIndex 리셋을 한 번에 (단일 배치 업데이트)
          setQuestionIndex((prev) => (prev + 1) % PLACEHOLDER_QUESTIONS.length);
          setCharIndex(0);
        }, PAUSE_BEFORE_NEXT_MS);
      }, PAUSE_AFTER_TYPED_MS);

      return () => {
        if (pauseTimerRef.current) clearTimeout(pauseTimerRef.current);
        if (nextTimerRef.current) clearTimeout(nextTimerRef.current);
      };
    }

    // 한 글자씩 타이핑
    const timer = setTimeout(() => {
      const nextCharIndex = charIndex + 1;
      setCharIndex(nextCharIndex);
      setAnimatedPlaceholder(currentQuestion.slice(0, nextCharIndex));
    }, TYPE_SPEED_MS);

    return () => clearTimeout(timer);
  }, [charIndex, questionIndex, isPaused, isCentered]);

  const handleFocus = () => {
    if (isCentered) setIsPaused(true);
  };

  const handleBlur = () => {
    // 입력창이 비어있을 때만 애니메이션 재개
    if (isCentered && !inputMessage) setIsPaused(false);
  };

  const placeholderText = isCentered ? animatedPlaceholder : '어떤 도움을 드릴까요?';

  const containerClass = isCentered
    ? "w-full"
    : "p-2 lg:p-4 border-t border-purple-100 bg-purple-50/30";

  const inputClass = isCentered
    ? "w-full border border-gray-300 rounded-full px-6 lg:px-8 py-2 lg:py-3 resize-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all text-base lg:text-lg shadow-sm"
    : "w-full h-10 lg:h-12 border border-gray-300 rounded-xl px-3 lg:px-4 py-2 resize-none overflow-hidden align-middle focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all text-sm lg:text-base leading-normal";

  return (
    <div className={containerClass}>
      <div className="flex gap-2 lg:gap-3 items-center">
        {/* 파일 업로드 버튼 (중앙 정렬이 아닐 때만 표시) */}
        {!isCentered && (
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex-shrink-0 w-10 h-10 lg:w-12 lg:h-12 flex items-center justify-center border border-gray-300 rounded-lg hover:bg-gray-50 hover:border-gray-400 transition-colors text-lg lg:text-xl"
            title="파일 업로드"
          >
            +
          </button>
        )}

        {/* 텍스트 입력 영역 */}
        <div className="flex-1 relative">
          <textarea
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            onKeyPress={onKeyPress}
            onFocus={handleFocus}
            onBlur={handleBlur}
            placeholder={placeholderText}
            aria-label="질문 입력"
            className={`${inputClass} ${isCentered ? 'pr-9 sm:pr-10' : ''}`}
            rows={1}
          />

          {/* 전송 버튼 (중앙 정렬일 때는 입력창 안에) */}
          {isCentered && (
            <button
              onClick={onSend}
              disabled={!inputMessage.trim() || isTyping}
              className="absolute right-2 top-[calc(50%-3px)] transform -translate-y-1/2 bg-gradient-to-r from-indigo-500 to-indigo-600 text-white w-7 h-7 sm:w-8 sm:h-8 lg:w-10 lg:h-10 rounded-full hover:from-indigo-600 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 flex items-center justify-center shadow-md"
              title="전송"
            >
              →
            </button>
          )}
        </div>

        {/* 전송 버튼 (중앙 정렬이 아닐 때) */}
        {!isCentered && (
          <button
            onClick={onSend}
            disabled={!inputMessage.trim() || isTyping}
            className="bg-gradient-to-r from-indigo-500 to-indigo-600 text-white px-3 lg:px-6 h-10 lg:h-12 rounded-xl hover:from-indigo-600 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 text-sm lg:text-base whitespace-nowrap flex items-center justify-center"
          >
            전송
          </button>
        )}
      </div>

      {/* 숨겨진 파일 입력 */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf"
        className="hidden"
        onChange={onFileChange}
      />
    </div>
  );
};

export default InputBar;
