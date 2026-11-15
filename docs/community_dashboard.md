# 어비스봇 · 커뮤니티 통합 정보 카드

이 기능은 서버 배너 정보와 **별도 메시지**입니다. 디스코드 정보 채널에
Components V2 카드 하나를 만들고 이후 **같은 메시지만 매시간 수정**합니다.
클립 기능은 계속 비활성화 상태로 유지합니다.

## 구성

- **방송 일정**: 기존 `notice_intelligence.py`가 카페 공지에서 확실한
  날짜와 시간을 검출한 `notice_schedule_state.json`을 표시합니다.
  미확인 일정은 만들어내지 않습니다. X 공지 자동 분석은 이 버전에 포함하지 않습니다.
- **기념일 / 주요 기록**: 서버의 `/home/ubuntu/chzzk-bot/community_milestones.json`에
  검증된 기록을 등록하면 D-day를 자동 계산합니다.
- **월간 방송 통계**: 치지직 폴링에서 새로 관측된 시작·종료 상태 전환을
  `community_live_sessions.json`에 기록합니다. 과거 방송을 추정하지
  않으며, 봇이 종료돼 관측하지 못한 구간은 전체 통계에서 제외될 수 있습니다.
  최대 24시간 이내 관측된 세션을 기록합니다.

### 기념일 파일 예시

아래 항목은 형식 예시이므로 실제 날짜를 검증해 바꿔서 사용하세요.

```json
{
  "events": [
    {"group": "hanayo", "title": "확인된 기념일 이름", "date": "2026-01-01"},
    {"group": "sakihane", "title": "확인된 기념일 이름", "date": "2026-02-01"}
  ]
}
```

설정은 `COMMUNITY_DASHBOARD_ENABLED=1`, `COMMUNITY_DASHBOARD_CHANNEL_ID`로
제어합니다. 한 번 생성된 메시지 ID는 `community_dashboard_state.json`에 저장됩니다.
`COMMUNITY_DASHBOARD_MESSAGE_ID`로 기존 메시지를 명시할 수도 있습니다.

복구할 때 상태 파일을 무작정 삭제하지 마세요. 봇은 최근 메시지 100개에서
전용 마커를 찾아 재사용하며, 목록 조회가 실패하거나 동일한 카드가 둘 이상
확인되면 **새 글을 만들지 않고 오류로 중단**합니다.

기존 알림·서버 배너·클립 메시지 ID는 변경하지 않습니다.
