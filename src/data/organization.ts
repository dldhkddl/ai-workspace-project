import type { Employee, Team } from '../types'

const initialConnection = (source: string | null) => ({
  status: 'not_connected' as const,
  source,
  lastCheckedAt: null,
  lastSuccessAt: null,
  error: null,
})

const initialWork = () => ({
  status: null,
  taskId: null,
  title: null,
  stage: null,
  progress: null,
})

const employee = (
  id: string,
  name: string,
  teamId: string,
  role: string,
  icon: string,
  plannedSource: string,
  plannedFeatures: string[],
): Employee => ({
  id,
  name,
  teamId,
  role,
  icon,
  connection: initialConnection(null),
  work: initialWork(),
  summary: null,
  alerts: [],
  allowedTools: [],
  approvalRequired: true,
  plannedSource,
  plannedFeatures,
})

export const employees: Employee[] = [
  employee('general-manager', '총괄 매니저', 'leadership', '전체 현황 요약과 중요 판단 요청', 'Crown', '운영센터 전체', ['전체 운영 요약', '중요 판단 요청']),
  employee('project-manager', 'PM', 'leadership', '목표 분해, 작업 배정, 일정과 의존성 관리', 'ClipboardList', '작업 관리 시스템', ['목표 분해', '작업 배정', '일정 관리']),
  employee('research-factcheck', '리서치·팩트체크', 'analysis', '외부 조사와 근거 확인', 'SearchCheck', '외부 조사 도구', ['자료 조사', '근거 확인']),
  employee('data-analyst', '데이터 분석', 'analysis', '운영 지표와 변화 분석', 'ChartNoAxesCombined', '운영 데이터 저장소', ['지표 분석', '변화 감지']),
  employee('tech-lead', '테크리드', 'development', '기술 설계와 변경 검토', 'Network', '개발 저장소', ['기술 설계', '변경 검토']),
  employee('frontend', '프론트엔드', 'development', '화면 구현과 사용자 경험', 'PanelsTopLeft', '개발 저장소', ['화면 구현', '접근성 점검']),
  employee('backend-integration', '백엔드·연동', 'development', 'API, DB, 기존 프로그램 연결', 'Cable', '기존 프로그램 API', ['API 연결', '데이터 동기화']),
  employee('ai-automation', 'AI·자동화', 'development', 'AI 실행기와 자동화 흐름', 'Bot', 'AI 실행 환경', ['AI 실행', '자동화 흐름']),
  employee('quality-assurance', 'QA', 'development', '기능, 회귀, 오류 검증', 'ShieldCheck', '시험 환경', ['기능 시험', '회귀 검증']),
  employee('devops-security', 'DevOps·보안', 'development', '실행 환경, 백업, 권한, 배포 안전', 'ServerCog', '실행·배포 환경', ['상태 감시', '백업 점검']),
  employee('ui-ux-designer', 'UI·UX 디자이너', 'design', '화면 구조와 시각 품질', 'Palette', '디자인 시스템', ['화면 설계', '디자인 검수']),
  employee('smartstore-operator', '스마트스토어 운영', 'operations', '주문, 취소, 반품, 배송, 재고 현황', 'Store', 'smartstore-ai', ['주문 조회', '재고 조회', '동기화 상태']),
  employee('scenario-writer', '시나리오 작가', 'story-production', '주제·구성·장면별 시나리오 작성', 'ScrollText', '스토리 제작 작업 공간', ['시나리오 기획', '장면 구성']),
  employee('story-editor', '스토리 에디터', 'story-production', '이야기 흐름·몰입도·일관성 편집', 'BookOpenCheck', '스토리 제작 작업 공간', ['구성 편집', '일관성 검토']),
  employee('prompt-director', '프롬프트 감독', 'story-production', '이미지·영상 생성 프롬프트 설계와 품질 통제', 'WandSparkles', '생성형 콘텐츠 작업 공간', ['프롬프트 설계', '생성 품질 검토']),
  employee('shorts-producer', '숏폼 제작', 'story-production', '영상 생성 상태와 결과 확인', 'Clapperboard', 'ai-shortform-generator', ['프로젝트 조회', '생성 상태 확인']),
  employee('youtube-channel', '유튜브 채널', 'operations', '조회수, 증가량, 영상 성과 확인', 'Youtube', 'YouTube Data API', ['영상 지표 조회', '수집 상태 확인']),
  employee('marketing-lead', '마케팅 총괄', 'marketing', '캠페인 방향과 우선순위', 'Megaphone', '마케팅 운영 데이터', ['캠페인 요약', '우선순위 제안']),
  employee('content-copy', '콘텐츠 기획·카피', 'marketing', '콘텐츠 주제, 대본, 문안 작성', 'PenTool', '콘텐츠 작업 공간', ['주제 기획', '초안 작성']),
  employee('performance-analyst', '성과 분석', 'marketing', '콘텐츠와 판매 성과 비교', 'ChartSpline', '콘텐츠·판매 데이터', ['성과 비교', '변화 분석']),
  employee('customer-inquiry', '문의·답변', 'customer-service', '고객 문의 분류와 답변 초안', 'MessagesSquare', '고객 문의 채널', ['문의 분류', '답변 초안']),
  employee('order-delivery', '주문·배송', 'customer-service', '주문과 배송 문의, 지연 확인', 'Truck', '주문·배송 시스템', ['주문 조회', '배송 확인']),
  employee('returns-voc', '교환·환불·VOC', 'customer-service', '교환, 환불 요청과 고객 의견 분석', 'RefreshCcw', '고객 지원 시스템', ['요청 분류', 'VOC 분석']),
  employee('stock-operator', '주식매매 운영', 'investment', '계좌, 포지션, 봇 상태 조회', 'CandlestickChart', 'paper-trading-bot', ['계좌 조회', '포지션 조회', '봇 상태']),
  employee('risk-analyst', '전략·위험 분석', 'investment', '손익, 낙폭, 위험 신호 분석', 'TriangleAlert', 'paper-trading-bot', ['손익 분석', '위험 신호 확인']),
  employee('obsidian-knowledge', '옵시디언 지식·기록', 'knowledge', '보고서, 결정, 작업 결과 기록', 'BookOpenText', '옵시디언 볼트', ['보고서 저장', '결정 기록']),
]

export const teams: Team[] = [
  { id: 'leadership', name: '총괄·프로젝트팀', shortName: '총괄실', color: '#7c3aed', employeeIds: ['general-manager', 'project-manager'] },
  { id: 'analysis', name: '정보분석팀', shortName: '분석실', color: '#2563eb', employeeIds: ['research-factcheck', 'data-analyst'] },
  { id: 'development', name: '개발팀', shortName: '개발실', color: '#0891b2', employeeIds: ['tech-lead', 'frontend', 'backend-integration', 'ai-automation', 'quality-assurance', 'devops-security'] },
  { id: 'design', name: '디자인팀', shortName: '디자인실', color: '#db2777', employeeIds: ['ui-ux-designer'] },
  { id: 'operations', name: '기존 프로그램 운영팀', shortName: '운영실', color: '#059669', employeeIds: ['smartstore-operator', 'youtube-channel'] },
  { id: 'story-production', name: 'AI 스토리 제작팀', shortName: '스토리팀', color: '#9333ea', employeeIds: ['scenario-writer', 'story-editor', 'prompt-director', 'shorts-producer'] },
  { id: 'marketing', name: '마케팅팀', shortName: '마케팅실', color: '#ea580c', employeeIds: ['marketing-lead', 'content-copy', 'performance-analyst'] },
  { id: 'customer-service', name: 'CS팀', shortName: 'CS실', color: '#ca8a04', employeeIds: ['customer-inquiry', 'order-delivery', 'returns-voc'] },
  { id: 'investment', name: '투자운영팀', shortName: '투자실', color: '#4f46e5', employeeIds: ['stock-operator', 'risk-analyst'] },
  { id: 'knowledge', name: '지식관리팀', shortName: '기록실', color: '#64748b', employeeIds: ['obsidian-knowledge'] },
]
