// frontend/src/App.js
import React, { useState, useRef, useEffect } from 'react';
import Sidebar from './components/Sidebar.jsx';
import MessageList from './components/MessageList.jsx';
import InputBar from './components/InputBar.jsx';
import HelpModal from './components/HelpModal.jsx';
import { Lightbulb } from 'lucide-react';
import './App.css';

// 랜딩 페이지 예시 질문
const EXAMPLE_QUESTIONS = [
  {
    label: "🏷️ 김치 수출 시 필수 라벨링 항목",
    query: "김치를 미국으로 수출할 때 라벨링에 필요한 요구사항을 알려주세요."
  },
  {
    label: "🏭 FDA 검사 준비 가이드",
    query: "FDA 검사에 대비하는 방법과 준비사항을 단계별로 알려주세요."
  },
  {
    label: "🚫 통관 거부 예방법",
    query: "FDA가 식품 수입을 거부하는 주요 사유와 이를 예방하는 방법을 알려주세요."
  }
];

const FDAChatbot = () => {
  // PWA 상태 관리
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [installPromptShown, setInstallPromptShown] = useState(false);  // 세션 내 1회 제어
  
  const [projects, setProjects] = useState([]);

  // 프로젝트별 메시지를 저장하는 객체 (초기 메시지 제거)
  const [projectMessages, setProjectMessages] = useState({});

  const [messages, setMessages] = useState([]);
  const [inputMessage, setInputMessage] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [isUsingSSE, setIsUsingSSE] = useState(false); // SSE 사용 여부 추적
  const [elapsedTime, setElapsedTime] = useState(0);
  const [showHelpModal, setShowHelpModal] = useState(false);
  const timerRef = useRef(null);
  const startTimeRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);
  const chatAreaRef = useRef(null);
  const eventSourceRef = useRef(null);  // SSE 중지 기능을 위한 외부 접근 ref

  const currentProject = projects.find(p => p.active);
  
  // PWA 설치 프롬프트 처리
  // Service Worker는 제거됨 - FDA 챗봇은 실시간 LLM 응답이 필요하므로 캐싱이 역효과.
  // 기존 등록된 SW는 public/sw.js의 kill switch가 자동 해제함.
  useEffect(() => {
    // 설치 프롬프트 이벤트 리스너
    // 이벤트 발생 시에는 deferredPrompt만 저장하고, 실제 표시는 첫 답변 완료 후 트리거
    const handleBeforeInstallPrompt = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

    // 온라인/오프라인 상태 감지
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  // 프로젝트가 변경될 때마다 메시지 로드
  useEffect(() => {
    if (currentProject) {
      const currentProjectMessages = projectMessages[currentProject.id] || [];
      setMessages(currentProjectMessages);
    } else {
      setMessages([]);
    }
  }, [currentProject?.id, projects]);

  

  useEffect(() => {
    if (chatAreaRef.current) {
      chatAreaRef.current.scrollTop = chatAreaRef.current.scrollHeight;
    }
  }, [messages]);

  // 타이머 함수들
  const startTimer = () => {
    startTimeRef.current = Date.now();
    setElapsedTime(0);
    timerRef.current = setInterval(() => {
      setElapsedTime(Date.now() - startTimeRef.current);
    }, 100);
  };

  const stopTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  // PWA 설치 함수
  const handleInstallApp = async () => {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      console.log(`PWA 설치 결과: ${outcome}`);
      setDeferredPrompt(null);
      setShowInstallPrompt(false);
    }
  };

  // 오프라인 상태에서 메시지 전송 시 처리
  const handleOfflineMessage = () => {
    const offlineMessage = {
      id: Date.now(),
      type: 'assistant',
      content: '현재 오프라인 상태입니다. 네트워크 연결을 확인해주세요.',
      timestamp: new Date().toISOString(),
      offline: true,
      citations: []  // ← 이 줄 추가!
    };
    setMessages(prev => [...prev, offlineMessage]);
  };

  const createNewProject = () => {
    const projectName = prompt('새 프로젝트 이름을 입력하세요:');
    if (projectName) {
      const newProjectId = Date.now();
      const newProject = {
        id: newProjectId,
        name: projectName,
        active: true,
        progress: 0
      };
      
      // 현재 메시지를 현재 프로젝트에 저장 (현재 프로젝트가 있는 경우에만)
      if (currentProject) {
        setProjectMessages(prev => ({
          ...prev,
          [currentProject.id]: messages
        }));
      }
      
      // 새 프로젝트 추가 (기존 프로젝트가 있으면 비활성화)
      setProjects(prev => {
        const updatedProjects = prev.map(p => ({ ...p, active: false }));
        return [...updatedProjects, newProject];
      });
      
      // 새 프로젝트의 빈 메시지 배열 생성
      setProjectMessages(prev => ({
        ...prev,
        [newProjectId]: []
      }));
      
      // 현재 화면 메시지를 빈 배열로 설정
      setMessages([]);
    }
  };

  const selectProject = (projectId) => {
    // 현재 메시지를 현재 프로젝트에 저장
    if (currentProject) {
      setProjectMessages(prev => ({
        ...prev,
        [currentProject.id]: messages
      }));
    }
    
    // 프로젝트 변경
    setProjects(prev => prev.map(p => ({ ...p, active: p.id === projectId })));
    
    // 선택된 프로젝트의 메시지 불러오기
    const selectedProjectMessages = projectMessages[projectId] || [];
    setMessages(selectedProjectMessages);
  };

  // API URL 가져오기 헬퍼 함수
  const getApiUrl = () => {
    // 빌드 시점 REACT_APP_API_URL이 있으면 그대로 사용 (로컬 개발용)
    if (process.env.REACT_APP_API_URL) {
      return process.env.REACT_APP_API_URL;
    }

    // 런타임 window.location 기반 동적 판단 (EC2 프로덕션 등)
    const origin = window.location.origin;

    // IP 직접 접속: :3001 → :8002 (docker-compose 포트 매핑과 일치)
    if (origin.includes(':3001')) {
      return origin.replace(':3001', ':8002');
    }

    // 도메인 접속: Nginx 리버스 프록시 뒤에 있다고 가정 → origin 그대로
    return origin;
  };

  // 답변 생성 중지 — SSE 연결 종료 + 현재까지 받은 토큰 보존
  const stopGeneration = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsGenerating(false);
    setIsUsingSSE(false);
    stopTimer();
    // status(로딩 인디케이터) 제거 + 스트리밍 메시지 완료 마킹
    // 토큰이 안 왔으면 "(답변이 중단되었습니다)" 봇 메시지를 새로 추가
    setMessages(prev => {
      const cleaned = prev
        .filter(msg => msg.type !== 'status')
        .map(msg => (msg.isStreaming ? { ...msg, isStreaming: false } : msg));

      const hasStreamingMessage = prev.some(msg => msg.isStreaming);
      if (!hasStreamingMessage) {
        cleaned.push({
          id: Date.now(),
          type: 'bot',
          content: '(답변이 중단되었습니다)',
          isStopped: true,
          isStreaming: false,
          timestamp: new Date().toISOString()
        });
      }
      return cleaned;
    });
  };

  // SSE를 사용한 스트리밍 메시지 전송
  const sendMessageSSE = (message, projectId, updatedMessages) => {
    return new Promise((resolve, reject) => {
      const query = encodeURIComponent(message);
      const projectParam = projectId ? `&project_id=${projectId}` : '';
      const url = `${getApiUrl()}/api/chat/stream?query=${query}${projectParam}`;
      
      console.log('SSE 연결 시작:', url);
      
      // SSE 사용 표시 (기존 로딩 박스 숨김)
      setIsUsingSSE(true);
      
      const eventSource = new EventSource(url);
      eventSourceRef.current = eventSource;  // 외부에서 중지 가능하도록 ref 저장
      
      // 상태 메시지를 저장할 임시 변수
      let currentStatusMessage = null;
      let finalResponse = null;
      let streamingMessageId = null;  // 토큰 스트리밍 중인 메시지 ID
      let accumulatedText = '';       // 스트리밍 중 누적된 텍스트

      // 상태 이벤트 리스너
      eventSource.addEventListener('status', (e) => {
        try {
          const data = JSON.parse(e.data);
          console.log('Status 이벤트:', data);

          // 상태 메시지 업데이트
          const statusMessages = {
            started: '질문을 분석하고 있습니다...',
            searching: 'FDA 문서를 검색하고 있습니다...',
            evaluating: '검색 결과를 평가하고 있습니다...',
            deep_search: '깊이 검색중... 정확한 답변을 찾고 있습니다',
            agent_complete: '추가 정보 수집 완료',
            generating: '답변을 생성하고 있습니다...',
            completed: '답변 생성 완료'
          };

          const statusMsg = statusMessages[data.status] || data.message;

          // 현재 상태 메시지가 없으면 새로 추가
          if (!currentStatusMessage) {
            currentStatusMessage = {
              id: Date.now() + 0.5,
              type: 'status',
              status: data.status,
              content: statusMsg,
              timestamp: new Date().toISOString()
            };

            setMessages(prev => [...prev, currentStatusMessage]);
          } else {
            // 기존 상태 메시지 업데이트
            setMessages(prev =>
              prev.map(msg =>
                msg.id === currentStatusMessage.id
                  ? { ...msg, status: data.status, content: statusMsg }
                  : msg
              )
            );
            currentStatusMessage.status = data.status;
            currentStatusMessage.content = statusMsg;
          }

        } catch (err) {
          console.error('Status 파싱 오류:', err);
        }
      });

      // 토큰 이벤트 리스너 (실시간 스트리밍)
      eventSource.addEventListener('token', (e) => {
        try {
          const data = JSON.parse(e.data);
          accumulatedText += data.chunk;

          if (!streamingMessageId) {
            // 첫 토큰: status 메시지를 bot 메시지로 교체
            streamingMessageId = Date.now() + 1;
            setMessages(prev => {
              const withoutStatus = prev.filter(msg => msg.id !== currentStatusMessage?.id);
              return [...withoutStatus, {
                id: streamingMessageId,
                type: 'bot',
                content: accumulatedText,
                isStreaming: true,
                timestamp: new Date().toISOString()
              }];
            });
          } else {
            // 이후 토큰: 기존 메시지에 텍스트 업데이트
            setMessages(prev =>
              prev.map(msg =>
                msg.id === streamingMessageId
                  ? { ...msg, content: accumulatedText }
                  : msg
              )
            );
          }
        } catch (err) {
          console.error('Token 파싱 오류:', err);
        }
      });

      // 결과 이벤트 리스너
      eventSource.addEventListener('result', (e) => {
        try {
          const data = JSON.parse(e.data);
          console.log('Result 이벤트:', data);

          finalResponse = {
            id: streamingMessageId || Date.now() + 1,
            type: 'bot',
            content: data.content,
            keywords: data.keywords || [],
            cfr_references: data.cfr_references || [],
            sources: data.sources || [],
            citations: data.citations || [],
            isStreaming: false,
            timestamp: new Date().toISOString()
          };

          // 스트리밍 메시지를 최종 답변으로 교체 (citations 추가)
          setMessages(prev => {
            const withoutOld = prev.filter(msg =>
              msg.id !== currentStatusMessage?.id && msg.id !== streamingMessageId
            );
            return [...withoutOld, finalResponse];
          });

          // 첫 답변 완료 시점에 PWA 설치 팝업 표시 (세션 내 1회, standalone 제외)
          if (
            !installPromptShown &&
            deferredPrompt &&
            !window.matchMedia('(display-mode: standalone)').matches
          ) {
            setShowInstallPrompt(true);
            setInstallPromptShown(true);
          }

          // SSE 사용 해제
          setIsUsingSSE(false);

          eventSource.close();
          eventSourceRef.current = null;
          resolve(finalResponse);

        } catch (err) {
          console.error('Result 파싱 오류:', err);
          setIsUsingSSE(false);
          eventSource.close();
          eventSourceRef.current = null;
          reject(err);
        }
      });
      
      // 에러 이벤트 리스너
      eventSource.addEventListener('error', (e) => {
        // 사용자가 stopGeneration으로 의도적 중지한 경우 — 에러 메시지 표시 생략
        if (eventSourceRef.current === null) {
          console.log('SSE 의도적 중지 — error 이벤트 무시');
          return;
        }

        console.error('SSE 에러:', e);

        try {
          if (e.data) {
            const errorData = JSON.parse(e.data);
            console.error('에러 메시지:', errorData.message);
          }
        } catch (err) {
          console.error('에러 파싱 실패:', err);
        }
        
        // 상태 메시지를 에러 메시지로 변경
        if (currentStatusMessage) {
          setMessages(prev => 
            prev.map(msg => 
              msg.id === currentStatusMessage.id 
                ? { 
                    ...msg, 
                    type: 'bot', 
                    content: '죄송합니다. 처리 중 오류가 발생했습니다. 다시 시도해주세요.',
                    error: true
                  }
                : msg
            )
          );
        }
        
        // SSE 사용 해제
        setIsUsingSSE(false);

        eventSource.close();
        eventSourceRef.current = null;
        reject(new Error('SSE 연결 오류'));
      });
      
      // 연결 열림
      eventSource.onopen = () => {
        console.log('SSE 연결 성공');
      };
    });
  };

  // 기존 API 호출 함수 (폴백용)
  const callChatAPI = async (message, projectId) => {
    try {
      const apiUrl = `${getApiUrl()}/api/chat`;
      console.log('API URL:', apiUrl); // 디버깅용

      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: message,
          project_id: projectId,
          language: 'ko'
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown server error' }));
        throw new Error(`HTTP error! status: ${response.status}, message: ${errorData.detail}`);
      }

      const data = await response.json();
      console.log('API Response:', data); // 디버깅용 - API 응답 전체 로그
      console.log('Citations:', data.citations); // 디버깅용 - citations만 로그
      return data;
    } catch (error) {
      console.error('API 호출 오류:', error);
      return {
        content: `죄송합니다. 서버 연결에 문제가 있습니다. 잠시 후 다시 시도해주세요.\n(에러: ${error.message})`,
        keywords: [],
        cfr_references: [],
        sources: []
      };
    }
  };

  const sendMessage = async (overrideMessage) => {
    const message = (typeof overrideMessage === 'string' ? overrideMessage : inputMessage).trim();
    if (!message) return;

    // 오프라인 상태 체크
    if (!isOnline) {
      handleOfflineMessage();
      return;
    }

    // 프로젝트가 없으면 자동으로 생성
    let activeProject = currentProject;
    if (!activeProject) {
      const now = new Date();
      const timeString = now.toLocaleTimeString('ko-KR', { 
        hour: '2-digit', 
        minute: '2-digit',
        hour12: false 
      });
      const projectName = `FDA 수출 문의 ${timeString}`;
      const newProjectId = Date.now();
      const newProject = {
        id: newProjectId,
        name: projectName,
        active: true,
        progress: 0
      };
      
      setProjects([newProject]);
      setProjectMessages(prev => ({
        ...prev,
        [newProjectId]: []
      }));
      activeProject = newProject;
    }

    const newUserMessage = {
      id: Date.now(),
      type: 'user',
      content: message
    };

    const updatedMessages = [...messages, newUserMessage];
    setMessages(updatedMessages);
    
    // 현재 프로젝트의 메시지도 업데이트
    setProjectMessages(prev => ({
      ...prev,
      [activeProject.id]: updatedMessages
    }));
    
    setInputMessage('');
    setIsGenerating(true);
    // 타이머 시작
    startTimer();

    try {
      // SSE를 사용한 스트리밍 호출
      await sendMessageSSE(message, activeProject.id, updatedMessages);
      
      // 타이머 정지
      stopTimer();
      
      // 최종 메시지는 sendMessageSSE 내부에서 이미 처리됨
      // 프로젝트 메시지 동기화
      setProjectMessages(prev => ({
        ...prev,
        [activeProject.id]: messages
      }));
      
    } catch (error) {
      stopTimer();
      setIsUsingSSE(false); // SSE 사용 해제
      console.error('메시지 전송 오류:', error);
      
      // SSE 실패 시 기존 API로 폴백
      console.log('SSE 실패, 기존 API로 폴백 시도...');
      
      try {
        const apiResponse = await callChatAPI(message, activeProject.id);
        
        const botMessage = {
          id: Date.now() + 1,
          type: 'bot',
          content: apiResponse.content,
          keywords: apiResponse.keywords || [],
          cfr_references: apiResponse.cfr_references || [],
          sources: apiResponse.sources || [],
          citations: apiResponse.citations || [],
          responseTime: apiResponse.responseTime || elapsedTime,
          agentResponseTime: apiResponse.agentResponseTime,
          timestamp: apiResponse.timestamp
        };
        
        const finalMessages = [...updatedMessages, botMessage];
        setMessages(finalMessages);
        
        // 프로젝트 메시지도 업데이트
        setProjectMessages(prev => ({
          ...prev,
          [activeProject.id]: finalMessages
        }));
        
      } catch (fallbackError) {
        console.error('폴백 API도 실패:', fallbackError);
        
        const errorMessage = {
          id: Date.now() + 1,
          type: 'bot',
          content: '죄송합니다. 응답을 생성하는데 문제가 발생했습니다.',
          keywords: [],
          cfr_references: [],
          citations: [],
          responseTime: elapsedTime
        };
        const finalMessages = [...updatedMessages, errorMessage];
        setMessages(finalMessages);
        
        // 프로젝트 메시지도 업데이트
        setProjectMessages(prev => ({
          ...prev,
          [activeProject.id]: finalMessages
        }));
      }
    } finally {
      setIsGenerating(false);
    }
  };

  // 컴포넌트 언마운트 시 타이머 정리
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, []);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const resetConversation = async () => {
    if (window.confirm('현재 대화를 초기화하시겠습니까?')) {
      try {
        await fetch(`${process.env.REACT_APP_API_URL}/api/project/${currentProject.id}/reset`, {
          method: 'POST',
        });
        setMessages([]);
        // 프로젝트 메시지도 초기화
        setProjectMessages(prev => ({
          ...prev,
          [currentProject.id]: []
        }));
      } catch (error) {
        console.error('대화 초기화 API 호출 오류:', error);
      }
    }
  };

  const handleFileUpload = (files) => {
    Array.from(files).forEach(file => {
      const uploadMessage = {
        id: Date.now() + Math.random(),
        type: 'user',
        content: `📎 파일 업로드됨: ${file.name}`,
        isFile: true
      };
      const updatedMessages = [...messages, uploadMessage];
      setMessages(updatedMessages);
      
      // 프로젝트 메시지도 업데이트
      setProjectMessages(prev => ({
        ...prev,
        [currentProject.id]: updatedMessages
      }));

      setTimeout(() => {
        const analysisMessage = {
          id: Date.now() + Math.random(),
          type: 'bot',
          content: `현재는 텍스트 질문만 지원하며, 파일 분석 기능은 준비 중입니다.`,
          cfr_references: [
            {
              title: '문서 분석 결과',
              description: '해당 인증서는 FDA 요구사항에 부합하는지 검토했습니다. 파일 분석 기능은 현재 개발 중입니다.'
            }
          ],
          citations: []  // ← 이 줄 추가!
        };
        const finalMessages = [...updatedMessages, analysisMessage];
        setMessages(finalMessages);
        
        // 프로젝트 메시지도 업데이트
        setProjectMessages(prev => ({
          ...prev,
          [currentProject.id]: finalMessages
        }));
      }, 1500);
    });
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    handleFileUpload(e.dataTransfer.files);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  

  const generateChecklist = () => {
    alert('체크리스트 생성 기능은 개발 중입니다.');
  };

  const downloadReport = () => {
    alert('보고서 다운로드 기능은 개발 중입니다.');
  };

  // 도움말에서 질문 선택 시 처리
  const handleHelpQuestionSelect = (question) => {
    setInputMessage(question);
    setShowHelpModal(false);
  };

  // 시간대별 인사말 생성
  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 6) {
      return "좋은 새벽이에요, 사용자님";
    } else if (hour < 12) {
      return "좋은 아침이에요, 사용자님";
    } else if (hour < 18) {
      return "좋은 오후에요, 사용자님";
    } else {
      return "좋은 저녁이에요, 사용자님";
    }
  };

  // 채팅 컨텐츠 렌더링
  const renderChatContent = () => {
    // 메시지가 없을 때는 클로드 스타일의 중앙 레이아웃
    if (messages.length === 0) {
      return (
        <>
        {/* 헤더 */}
        <div className="p-2 lg:p-4 border-b border-purple-100 bg-purple-50/30 pl-16 lg:pl-4">
          <div className="flex flex-row justify-between items-center gap-3">
            <div className="hidden md:flex items-center gap-3">
              <p className="text-xs lg:text-sm text-gray-600 leading-relaxed">
                FDA 공식 문서를 바탕으로 정확한 정보를 제공합니다.
              </p>
            </div>
            <div className="flex items-center gap-3 ml-auto">
              <button
                onClick={() => setShowHelpModal(true)}
                className="flex items-center gap-2 bg-yellow-50 hover:bg-yellow-100 text-yellow-700 px-3 lg:px-4 py-2 rounded-lg border border-yellow-200 hover:border-yellow-300 transition-colors"
              >
                <Lightbulb className="w-4 h-4" />
                <span className="text-xs lg:text-sm font-medium">무엇을 물어볼까요?</span>
              </button>
            </div>
          </div>
        </div>

          {/* 중앙 환영 영역 */}
          <div className="flex-1 flex items-center justify-center p-3 lg:p-6">
            <div className="text-center w-full">
              <h2 className="text-2xl lg:text-3xl font-bold text-gray-800 mb-3 lg:mb-4">
                {getGreeting()}
              </h2>
              <p className="text-gray-600 text-base lg:text-lg mb-6 lg:mb-8">FDA 식품 수출 규제에 대해 무엇이든 물어보세요</p>
              
              {/* 중앙 입력창 */}
              <div className="max-w-3xl mx-auto">
                <InputBar
                  inputMessage={inputMessage}
                  setInputMessage={setInputMessage}
                  isTyping={isGenerating}
                  onSend={sendMessage}
                  onKeyPress={handleKeyPress}
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  dragOver={dragOver}
                  fileInputRef={fileInputRef}
                  onFileChange={(e) => handleFileUpload(e.target.files)}
                  isCentered={true}
                />

                {/* 예시 질문 버튼 그룹 */}
                <div className="mt-4 flex flex-col md:flex-row md:justify-center gap-2 md:gap-3 flex-wrap">
                  {EXAMPLE_QUESTIONS.map((example, idx) => (
                    <button
                      key={idx}
                      type="button"
                      title={example.query}
                      onClick={() => {
                        setInputMessage('');
                        sendMessage(example.query);
                      }}
                      disabled={isGenerating}
                      className="px-4 py-2 text-sm text-gray-700 bg-white border border-purple-100 rounded-3xl shadow-sm hover:bg-purple-50 hover:border-purple-200 hover:-translate-y-0.5 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {example.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </>
      );
    }

    // 메시지가 있을 때는 일반 채팅 레이아웃
    return (
      <>
        {/* 헤더 */}
        <div className="p-2 lg:p-4 border-b border-purple-100 bg-purple-50/30 pl-16 lg:pl-4">
          <div className="flex flex-row justify-between items-center gap-3">
            {/* 제목 영역: 모바일 숨김, PC(lg)에서만 표시 */}
            <div className="hidden lg:flex items-center gap-3">
              {currentProject ? (
                <div className="flex items-center gap-2">
                  <h1 className="text-lg lg:text-xl font-semibold text-gray-800">{currentProject.name}</h1>
                  <button
                    onClick={() => {
                      const newName = prompt('프로젝트 이름을 변경하세요:', currentProject.name);
                      if (newName && newName.trim()) {
                        console.log('프로젝트 이름 변경:', currentProject.name, '->', newName.trim());
                        setProjects(prev => prev.map(p =>
                          p.id === currentProject.id ? { ...p, name: newName.trim() } : p
                        ));
                      }
                    }}
                    className="text-gray-400 hover:text-gray-600 text-sm px-2 py-1 rounded hover:bg-gray-100 transition-colors"
                    title="프로젝트 이름 변경"
                  >
                    ✏️
                  </button>
                </div>
              ) : (
                <h1 className="text-lg lg:text-xl font-semibold text-gray-800">FDA Export Assistant</h1>
              )}
            </div>
            <div className="flex items-center gap-2 lg:gap-3 ml-auto">
              {currentProject && (
                <button
                  onClick={resetConversation}
                  className="text-gray-500 hover:text-gray-700 text-sm px-3 py-1 rounded border border-gray-300 hover:border-gray-400 transition-colors"
                >
                  대화 초기화
                </button>
              )}
              <button
                onClick={() => setShowHelpModal(true)}
                className="flex items-center gap-2 bg-yellow-50 hover:bg-yellow-100 text-yellow-700 px-3 lg:px-4 py-2 rounded-lg border border-yellow-200 hover:border-yellow-300 transition-colors"
              >
                <Lightbulb className="w-4 h-4" />
                <span className="text-xs lg:text-sm font-medium">무엇을 물어볼까요?</span>
              </button>
            </div>
          </div>
        </div>


        {/* 채팅 영역 */}
        <div ref={chatAreaRef} className="flex-1 p-0 overflow-y-auto">
          <MessageList
            messages={messages}
            isTyping={isGenerating}
            isUsingSSE={isUsingSSE}
            elapsedTime={elapsedTime}
            onGenerateChecklist={generateChecklist}
            onDownloadReport={downloadReport}
            setInputMessage={setInputMessage}
            sendMessage={sendMessage}
          />
        </div>

        {/* 입력 영역 */}
        <InputBar
          inputMessage={inputMessage}
          setInputMessage={setInputMessage}
          isTyping={isGenerating}
          onSend={sendMessage}
          onStop={stopGeneration}
          onKeyPress={handleKeyPress}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          dragOver={dragOver}
          fileInputRef={fileInputRef}
          onFileChange={(e) => handleFileUpload(e.target.files)}
        />
      </>
    );
  };

  const deleteProject = async (projectId) => {
    if (projects.length <= 1) {
      // 마지막 프로젝트를 삭제하면 프로젝트가 없는 상태로 변경
      setProjects([]);
      setMessages([]);
      return;
    }
    
    if (window.confirm('정말 이 프로젝트를 삭제하시겠습니까?')) {
      try {
        await fetch(`${process.env.REACT_APP_API_URL}/api/project/${projectId}`, {
          method: 'DELETE',
        });
      } catch (error) {
        console.error('프로젝트 삭제 API 호출 오류:', error);
      }
      
      const deletingActiveProject = projects.find(p => p.id === projectId)?.active;
      
      // 프로젝트 메시지도 함께 삭제
      setProjectMessages(prev => {
        const newMessages = { ...prev };
        delete newMessages[projectId];
        return newMessages;
      });
      
      setProjects(prev => {
        const remaining = prev.filter(p => p.id !== projectId);
        
        if (deletingActiveProject && remaining.length > 0) {
          remaining[0].active = true;
          // 첫 번째 남은 프로젝트의 메시지 불러오기
          const firstProjectMessages = projectMessages[remaining[0].id] || [];
          setMessages(firstProjectMessages);
        }
        
        return remaining;
      });
    }
  };

  

  return (
    <div className="flex h-screen bg-gray-100">
      {/* 오프라인 상태 표시 */}
      {!isOnline && (
        <div className="fixed top-0 left-0 right-0 z-50 bg-red-500 text-white text-center py-2 text-sm">
          📱 오프라인 상태입니다. 일부 기능이 제한될 수 있습니다.
        </div>
      )}

      {/* PWA 설치 프롬프트 — 헤더 아래로 위치 */}
      {showInstallPrompt && (
        <div className="fixed top-16 lg:top-20 right-4 z-50 bg-white border border-gray-200 rounded-lg shadow-lg p-4 max-w-sm">
          <div className="flex items-start gap-3">
            <div className="flex-shrink-0">
              <div className="w-8 h-8 bg-indigo-100 rounded-full flex items-center justify-center">
                📱
              </div>
            </div>
            <div className="flex-1">
              <h3 className="font-medium text-gray-900 text-sm">앱으로 설치하기</h3>
              <p className="text-gray-600 text-xs mt-1">홈 화면에 추가하여 더 편리하게 사용하세요.</p>
              <div className="flex gap-2 mt-3">
                <button
                  onClick={handleInstallApp}
                  className="bg-indigo-500 text-white px-3 py-1 rounded text-xs hover:bg-indigo-600 transition-colors"
                >
                  설치
                </button>
                <button
                  onClick={() => setShowInstallPrompt(false)}
                  className="text-gray-500 px-3 py-1 rounded text-xs hover:bg-gray-100 transition-colors"
                >
                  나중에
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 사이드바 */}
      <Sidebar
        projects={projects}
        onCreateProject={createNewProject}
        onSelectProject={selectProject}
        onDeleteProject={deleteProject}
      />

      {/* 메인 컨텐츠 - 넓은 레이아웃 */}
      <div className="flex-1 flex justify-center lg:ml-0 ml-0">
        <div className="w-full max-w-5xl flex flex-col bg-white">
          {renderChatContent()}
        </div>
      </div>

      {/* 도움말 모달 */}
      <HelpModal
        isOpen={showHelpModal}
        onClose={() => setShowHelpModal(false)}
        onSelectQuestion={handleHelpQuestionSelect}
        onSendMessage={sendMessage}
      />
    </div>
  );
};

export default FDAChatbot;