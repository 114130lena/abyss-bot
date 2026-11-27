# 통나무 파워
기존 알림 프로세스와 독립 실행. 지정 채널 1575809466576932904 외 명령 사용 금지. 현금/상품 교환 불가.
Python 3.11 이상, 별도 가상환경에서 pip install -r log_power/requirements.txt.
Discord Developer Portal에서 Server Members Intent 활성화. Bot에 View Channel, Send Messages, Embed Links, Read Message History, Manage Messages(핀) 권한 부여. 기존 HTTP interactions endpoint가 있다면 버튼 이벤트 전달 충돌 여부를 실제 테스트하기 전 변경하지 말 것.
DISCORD_BOT_TOKEN 및 LOG_POWER_DB를 비공개 환경 파일에 설정. DB는 저장소 밖의 영구 경로 사용.
python -m log_power.bot
systemd 서비스 예시: 별도 사용자 및 경로로 조정 후 설치. 기존 서비스 중지 금지.
SQLite 온라인 백업: python -m log_power.backup. 백업 디렉터리는 비공개.
현재 기본: 두 명 이상 음성 채널, AFK 제외, 30분당 500, 일일 1000, 최소 참여 100, 한 번에 예측 하나, 정산 후 5분 삭제. 관리자 지급은 1회 1인당 1억 상한(오입력 방지). 서버 전체 지급에는 members intent 필요.
운영센터 공개 통계 연동, 개인 상세 거래 조회, 지급 정정 및 보상 설정 변경은 후속 구현 대상.
