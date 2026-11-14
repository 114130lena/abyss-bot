# 운영 워크플로 분류 및 검증 기준

이 문서는 **파일명을 바꾸거나 비활성화하지 않는** 관리용 분류표입니다. 실제 `on:` 트리거는 각각의 워크플로에서 확인해야 합니다. 분류는 이름과 용도 기반이며 자동 실행 여부를 의미하지 않습니다.

## 배포 기준

- `runtime_files.txt`를 50개 Python 런타임 파일과 2개 텍스트 파일의 단일 목록으로 사용합니다.
- `deploy.yml`은 같은 목록으로 로컬 문법 검사, 원격 업로드, 스테이징 검사, 운영 반영, 백업 및 롤백을 수행합니다.
- `.env`, 디스코드 인증 정보, 운영 중 생성되는 JSON과 미디어 파일은 이 목록에 절대 추가하지 않습니다.
- 기본 브랜치 병합 전에는 테스트가 모두 통과해야 하며 운영 서버의 배포 재시작은 병합 후 별도 확인합니다.

## 워크플로 목록 (64개)

### 회귀 검증 (5개)
- `abyss-integration-tests.yml`
- `main-x-photo-tests.yml`
- `media-clip-tests.yml`
- `production-media-routing-test.yml`
- `x-media-tests.yml`

### 수동 진단·일회성 (25개)
- `all-platform-latency-diagnose.yml`
- `all-platform-rules-audit.yml`
- `cafe-video-media-deploy.yml`
- `chzzk-two-preview.yml`
- `comprehensive-alert-preview.yml`
- `diagnose-huya-latest-fanart.yml`
- `diagnose-recurrent-x-photo-fanart.yml`
- `discord-issue-preview.yml`
- `enable-hiyorireplay-channel.yml`
- `fanart-permissions-audit.yml`
- `fanart-recycle-verify.yml`
- `fanart-url-deploy.yml`
- `fanart-url-preview.yml`
- `five-footer-buttons-deploy.yml`
- `hiyori-channel-map-deploy.yml`
- `media-overwrite-inspect.yml`
- `media-production-path-audit.yml`
- `official-profiles-deploy.yml`
- `one-repost-photo-test.yml`
- `photo-fanart-exclusion-deploy.yml`
- `photo-safe-repost-cafe-deploy.yml`
- `profile-rotation-preview.yml`
- `test-image-card.yml`
- `youtube-fast-retry-deploy.yml`
- `youtube-health-diagnose.yml`

### 운영·배포 (4개)
- `all-platform-self-heal-deploy.yml`
- `deploy-strict-x-native-media.yml`
- `deploy.yml`
- `operational-audit.yml`

### 정기·관리 (9개)
- `banner-info-tests.yml`
- `banner-secret-tests.yml`
- `clip-paired-layout-once.yml`
- `clip-schedule-health.yml`
- `fanart-weekly-cleanup.yml`
- `guild-banner-health-monitor.yml`
- `guild-banner-replace-once.yml`
- `guild-banner-schedule.yml`
- `media-sync.yml`

### X·Bluebird 점검/수동작업 (20개)
- `bluebird-auth-shadow-test.yml`
- `bluebird-auth-status-diagnose.yml`
- `bluebird-live-bridge-activate.yml`
- `bluebird-live-health-verify.yml`
- `bluebird-shadow-test.yml`
- `fx-feed-format-diagnose.yml`
- `fx-repost-timeline-deploy.yml`
- `historical-repost-compare.yml`
- `repost-delivery-gap-diagnose.yml`
- `repost-photo-examples-audit.yml`
- `repost-photo-first-deploy.yml`
- `x-alternative-media-diagnose.yml`
- `x-current-health-diagnose.yml`
- `x-fast-photos-deploy.yml`
- `x-image-aware-guard-deploy.yml`
- `x-latency-deploy.yml`
- `x-latency-diagnostic.yml`
- `x-media-unknown-safeguard-deploy.yml`
- `x-repost-latency-diagnostics-deploy.yml`
- `x-video-banner-only-deploy.yml`

### 기타 (1개)
- `visual-flags-diagnostic.yml`

## 읽기 전용 운영 검증

1. 운영 프로세스 `chzzk-bot.service`, 미디어 타이머, Bluebird 컨테이너의 실행 여부를 확인합니다.
2. 카페/X 수집 성공 시각과 연속 실패 횟수를 확인합니다. RSS 정상 응답과 실제 신규 게시물 배송 성공은 별개로 기록합니다.
3. 치지직 라이브·유튜브 업로드·WebSub·카페·X 원문·리트윗을 각각 확인합니다.
4. 디스코드 전역 전송 기록(`sent`/`pending`/`uncertain`)과 X 전용 재게시 기록을 읽기 전용으로 확인합니다. 미확정 항목을 자동으로 다시 전송하지 않습니다.
5. 서버 배너는 08:00/18:00 KST 스케줄, 이미지-안내 카드 동기화, 수동 교체 후 다음 정기 일정 복귀 여부를 확인합니다.
6. 클립은 10개 후보, 메시지 갱신, 최근 수집·게시 시각을 확인합니다.
7. 오류가 있어도 재전송을 통해 시험하지 않습니다. 운영 메시지 ID와 중복 기록을 비교한 뒤 안전한 별도 수정으로 처리합니다.

## 확인된 리스크

- `resilience.py`는 읽기 시간 초과 및 연결 단절 때 자동 재전송을 중지하지만 HTTP 500/502/503/504에는 재시도를 수행합니다. 서버가 메시지를 수락하고 오류를 반환한 경우 이중 전송 가능성을 별도로 테스트해야 합니다.
- 성공으로 종료된 워크플로는 그 실행 시점의 상태만 확인합니다. 전체 플랫폼의 실시간 지연·누락률 보증은 아닙니다.
