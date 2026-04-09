import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import useTypingEffect from '../hooks/useTypingEffect';
import TermTooltip from './TermTooltip';
import CitationLink from './CitationLink';

const TypingMessage = ({ message, speed = 15, citations }) => {
  // SSE 토큰 스트리밍 시 타이핑 효과 비활성화 — 토큰 도착 자체가 타이핑 역할
  const skipTyping = message.isStreaming !== undefined;
  const { displayText: typedText, isComplete: typingComplete } = useTypingEffect(
    skipTyping ? '' : message.content,
    speed
  );
  const displayText = skipTyping ? message.content : typedText;
  const isComplete = skipTyping ? !message.isStreaming : typingComplete;
  
  // FDA 용어를 감지하고 툴팁으로 감싸는 함수
  const renderTextWithTerms = (text) => {
    if (!text) return text;
    
    // FDA 용어 목록 (정규식으로 정확한 매칭)
    const fdaTerms = ['FSVP', 'GRAS', 'RPM', 'GWPE', 'HACCP', 'CGMP', 'FDA', 'CFR'];
    const termRegex = new RegExp(`\\b(${fdaTerms.join('|')})\\b`, 'gi');
    
    const parts = text.split(termRegex);
    const matches = text.match(termRegex) || [];
    
    return parts.map((part, index) => {
      if (index < matches.length) {
        const term = matches[index].toUpperCase();
        return (
          <React.Fragment key={index}>
            {part}
            <TermTooltip term={term} />
          </React.Fragment>
        );
      }
      return part;
    });
  };
  
  // 마크다운 문법을 파싱하는 함수
  const parseMarkdown = (text) => {
    if (!text) return text;
    
    // 굵은 글씨 처리: **텍스트** -> <strong>텍스트</strong>
    let processedText = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // 기울임 처리: *텍스트* -> <em>텍스트</em>
    processedText = processedText.replace(/\*(.*?)\*/g, '<em>$1</em>');
    
    return processedText;
  };

  return (
    <div className="whitespace-pre-wrap">
      {(() => {
        // [1], [2] 형식과 [출처 4] 형식 모두 매칭
        const citationRegex = /\[(?:출처\s*)?(\d+)\]/g;
        const parts = [];
        let lastIndex = 0;
        let match;
        let key = 0;

        while ((match = citationRegex.exec(displayText)) !== null) {
          if (match.index > lastIndex) {
            const textPart = displayText.substring(lastIndex, match.index);
            // 마크다운 파싱 후 FDA 용어 처리
            const markdownParsed = parseMarkdown(textPart);
            parts.push(
              <span key={`text-${key++}`} dangerouslySetInnerHTML={{ __html: markdownParsed }} />
            );
          }

          const citationNum = parseInt(match[1]);
          const citation = citations?.find(c => c.index === citationNum);
          
          parts.push(
            <CitationLink
              key={`cite-${key++}`}
              number={citationNum}
              url={citation?.url}
              title={citation?.title}
            />
          );

          lastIndex = match.index + match[0].length;
        }

        if (lastIndex < displayText.length) {
          const textPart = displayText.substring(lastIndex);
          // 마크다운 파싱 후 FDA 용어 처리
          const markdownParsed = parseMarkdown(textPart);
          parts.push(
            <span key={`text-${key++}`} dangerouslySetInnerHTML={{ __html: markdownParsed }} />
          );
        }

        return parts;
      })()}
      {!isComplete && <span className="animate-pulse">|</span>}
    </div>
  );
};

export default TypingMessage;
