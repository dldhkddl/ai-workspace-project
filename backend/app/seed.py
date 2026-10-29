from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Employee, EmployeeConnection, EmployeeWorkState, Team


TEAM_SEED = [
    ("leadership", "총괄·프로젝트팀", "총괄팀", "#7c3aed"),
    ("analysis", "정보분석팀", "분석팀", "#2563eb"),
    ("development", "개발팀", "개발팀", "#0891b2"),
    ("design", "디자인팀", "디자인실", "#db2777"),
    ("operations", "기존 프로그램 운영팀", "운영팀", "#059669"),
    ("story-production", "AI 스토리 제작팀", "스토리팀", "#9333ea"),
    ("marketing", "마케팅팀", "마케팅실", "#ea580c"),
    ("customer-service", "CS팀", "CS팀", "#ca8a04"),
    ("investment", "투자운영팀", "투자팀", "#4f46e5"),
    ("knowledge", "지식관리팀", "기록팀", "#64748b"),
]

EMPLOYEE_SEED = [
    ("general-manager", "총괄 매니저", "leadership", "전체 상황 요약과 중요 판단 검토", "Crown", "AI 운영센터 전체", ["전체 운영 요약", "중요 판단 검토"]),
    ("project-manager", "프로젝트 매니저", "leadership", "목표 분석·작업 배정·일정 관리", "ClipboardList", "작업 관리 시스템", ["목표 분석", "작업 배정", "일정 관리"]),
    ("research-factcheck", "리서치·팩트체크", "analysis", "자료 조사와 근거 확인", "SearchCheck", "인터넷 조사 도구", ["자료 조사", "근거 확인"]),
    ("data-analyst", "데이터 분석", "analysis", "운영 지표와 변화 분석", "ChartNoAxesCombined", "운영 데이터 저장소", ["지표 분석", "변화 감지"]),
    ("tech-lead", "테크 리드", "development", "기술 설계와 변경 검토", "Network", "개발 저장소", ["기술 설계", "변경 검토"]),
    ("frontend", "프론트엔드", "development", "화면 구현과 사용자 경험", "PanelsTopLeft", "개발 저장소", ["화면 구현", "접근성 점검"]),
    ("backend-integration", "백엔드·연동", "development", "API·DB·기존 프로그램 연결", "Cable", "기존 프로그램 API", ["API 연결", "데이터 동기화"]),
    ("ai-automation", "AI 자동화", "development", "AI 실행 기반과 자동화 흐름", "Bot", "AI 실행 환경", ["AI 실행", "자동화 흐름"]),
    ("quality-assurance", "QA", "development", "기능·통합·오류 검증", "ShieldCheck", "테스트 환경", ["기능 테스트", "통합 검증"]),
    ("devops-security", "DevOps·보안", "development", "실행 환경·백업·권한·배포 안전", "ServerCog", "실행·배포 환경", ["상태 감시", "백업 관리"]),
    ("ui-ux-designer", "UI·UX 디자이너", "design", "화면 구조와 시각 경험", "Palette", "디자인 시스템", ["화면 설계", "디자인 검토"]),
    ("smartstore-operator", "스마트스토어 운영", "operations", "주문·취소·반품·발송·재고 현황", "Store", "smartstore-ai", ["주문 조회", "재고 조회", "동기화 상태"]),
    ("scenario-writer", "시나리오 작가", "story-production", "주제·구성·장면별 시나리오 작성", "ScrollText", "스토리 제작 작업 공간", ["시나리오 기획", "장면 구성"]),
    ("story-editor", "스토리 에디터", "story-production", "이야기 흐름·몰입도·일관성 편집", "BookOpenCheck", "스토리 제작 작업 공간", ["구성 편집", "일관성 검토"]),
    ("prompt-director", "프롬프트 감독", "story-production", "이미지·영상 생성 프롬프트 설계와 품질 통제", "WandSparkles", "생성형 콘텐츠 작업 공간", ["프롬프트 설계", "생성 품질 검토"]),
    ("shorts-producer", "숏폼 제작", "story-production", "영상 생성 상태와 결과 확인", "Clapperboard", "ai-shortform-generator", ["프로젝트 조회", "생성 상태 확인"]),
    ("youtube-channel", "유튜브 채널", "operations", "조회수·증가율·영상 성과 확인", "Youtube", "YouTube Data API", ["영상 지표 조회", "수집 상태 확인"]),
    ("marketing-lead", "마케팅 총괄", "marketing", "캠페인 방향과 우선순위", "Megaphone", "마케팅 운영 데이터", ["캠페인 요약", "우선순위 제안"]),
    ("content-copy", "콘텐츠 기획·카피", "marketing", "콘텐츠 주제와 초안 작성", "PenTool", "콘텐츠 작업 공간", ["주제 기획", "초안 작성"]),
    ("performance-analyst", "성과 분석", "marketing", "콘텐츠와 판매 성과 비교", "ChartSpline", "콘텐츠·판매 데이터", ["성과 비교", "변화 분석"]),
    ("customer-inquiry", "문의·응대", "customer-service", "고객 문의 분류와 응답 초안", "MessagesSquare", "고객 문의 채널", ["문의 분류", "응답 초안"]),
    ("order-delivery", "주문·배송", "customer-service", "주문·배송 문의와 지연 확인", "Truck", "주문·배송 시스템", ["주문 조회", "배송 확인"]),
    ("returns-voc", "교환·반품·VOC", "customer-service", "교환·반품 요청과 고객 의견 분석", "RefreshCcw", "고객 지원 시스템", ["요청 분류", "VOC 분석"]),
    ("stock-operator", "주식매매 운영", "investment", "계좌·자산·운영 상태 조회", "CandlestickChart", "paper-trading-bot", ["계좌 조회", "자산 조회", "봇 상태"]),
    ("risk-analyst", "전략·위험 분석", "investment", "손익·변동·위험 신호 분석", "TriangleAlert", "paper-trading-bot", ["손익 분석", "위험 신호 확인"]),
    ("obsidian-knowledge", "옵시디언 지식관리", "knowledge", "보고서·결정·작업 결과 기록", "BookOpenText", "옵시디언 볼트", ["보고서 저장", "결정 기록"]),
]


async def seed_database(session: AsyncSession) -> None:
    existing_teams = {team.id: team for team in (await session.scalars(select(Team))).all()}
    for team_id, name, short_name, color in TEAM_SEED:
        team = existing_teams.get(team_id) or Team(id=team_id)
        team.name, team.short_name, team.color = name, short_name, color
        session.add(team)
    await session.flush()

    existing_employees = {employee.id: employee for employee in (await session.scalars(select(Employee))).all()}
    for employee_id, name, team_id, role, icon, source, features in EMPLOYEE_SEED:
        employee = existing_employees.get(employee_id) or Employee(id=employee_id)
        employee.team_id = team_id
        employee.name, employee.role, employee.icon = name, role, icon
        employee.summary = None
        employee.allowed_tools = []
        employee.approval_required = True
        employee.planned_source = source
        employee.planned_features = features
        session.add(employee)
        if employee_id not in existing_employees:
            session.add(EmployeeConnection(employee_id=employee_id, status="not_connected"))
            session.add(EmployeeWorkState(employee_id=employee_id, status=None))
    await session.commit()
