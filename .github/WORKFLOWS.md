# 어비스봇 GitHub 관리 안내

GitHub의 **화면에 표시되는 이름은 한국어**, 코드·파일명·이벤트 식별자는 **영어**로 유지합니다.
이 문서는 2026-11-11 기준 워크플로 64개를 분류한 관리 안내입니다.

## Actions 이름 표준

`분류 · 수행 내용` 형식으로 작성합니다. 작업 유형(진단/검증/적용/배포/미리보기/일회성)을 제목만 보고 구분할 수 있어야 합니다.

| 분류 | 용도 | 대표 워크플로 파일 |
| --- | --- | --- |
| 운영 | 어비스봇 자동배포, 장애 복구, 서버 상태 | `deploy.yml`, `operational-audit.yml` |
| 검증 | 배포 전 회귀 테스트 | `abyss-integration-tests.yml`, `x-media-tests.yml` |
| X | 본문·리트윗·Bluebird 수집 | `bluebird-live-bridge-activate.yml`, `x-latency-diagnostic.yml` |
| 치지직 | 라이브 알림 미리보기 | `chzzk-two-preview.yml` |
| 유튜브 | 업로드 알림·재시도·다시보기 | `youtube-health-diagnose.yml` |
| 카페 | 게시글 및 영상 처리 | `cafe-video-media-deploy.yml` |
| 배너 | 서버 배너 교체·안내 메시지 | `guild-banner-schedule.yml`, `guild-banner-health-monitor.yml` |
| 팬아트 | 원본 링크·사용 이력·후보 | `fanart-recycle-verify.yml`, `fanart-weekly-cleanup.yml` |
| 클립 | 클립 메시지·정기 업데이트 | `clip-schedule-health.yml` |
| 미디어 | 사진·영상 검증 및 전달 | `media-production-path-audit.yml` |
| 알림 | 통합 UI·버튼·채널 연결 | `comprehensive-alert-preview.yml` |
| 프로필 | 공식 이미지·랜덤 교체 | `official-profiles-deploy.yml` |
| 디스코드 | 요청 기반 테스트 미리보기 | `discord-issue-preview.yml` |

## 실제로 운영 중인 작업과 일회성 작업

- `운영 · 어비스봇 자동배포`는 기본 봇 코드 배포용입니다. 이 작업의 **파일 경로와 트리거는 변경하지 않습니다**.
- `X · Bluebird 수집기 활성화`는 운영 중인 수집 컨테이너의 설정/재기동용입니다. 이름이 비슷한 **비로그인 비교 테스트, 로그인 일회성 테스트**와 구분합니다.
- `배너 · 서버 배너 정기 교체`는 원래 봇의 정기 배너 일정과 별개인 예비 점검 워크플로이므로, 제목은 스케줄 관련이고 운영 주체는 설정을 확인해야 합니다.
- 기존 `[abyss-test]`, `[abyss-activate]`, `[abyss-diagnose]` 같은 Issue 제목은 자동화의 **정확한 실행 키**일 수 있습니다. 자동화 실행 전에는 임의로 변경하지 마세요.
- 역사적 Issue/PR/커밋/실행 기록은 삭제하거나 강제로 수정하지 않습니다. 완료한 테스트·진단 Issue는 종료해서 현재 열린 이슈만 쉽게 볼 수 있도록 합니다.

## 새 워크플로 작성 규칙

1. `.github/workflows/*.yml` 파일명은 기존 영어 소문자와 하이픈 스타일을 따릅니다.
2. 워크플로 최상위 `name:`은 `분류 · 수행 내용`의 한국어 표시 이름으로 작성합니다.
3. `on:`의 트리거, `jobs.*`의 식별자, `permissions`, `secrets`, `issue.title`의 일치 조건은 이름 정리를 이유로 바꾸지 않습니다.
4. 일회성 점검은 결과가 확인되면 Issue를 종료하고 실제 운영 작업과 혼동되지 않게 설명합니다.
5. 동작 변경이나 워크플로 삭제는 **표시 이름 변경과 반드시 별도 PR**에서 검증합니다.

## 가장 먼저 살펴볼 곳

- 일반 운영 장애: **Actions → 운영 · 어비스봇 자동배포**
- X 본문·리트윗 누락: **Actions → X · Bluebird 운영 연동 검증** 및 **X · 현재 수집 상태 점검**
- 배너 미교체: **Actions → 배너 · 서버 배너 상태 감시**
- 클립 미갱신: **Actions → 클립 · 정기 게시 일정 점검**

참고: GitHub Actions 과거 실행 결과, 종료된 PR 및 이전 커밋에는 옛 이름이나 영문 커밋 메시지가 그대로 남을 수 있습니다. 이는 실행 기록을 보존하기 위한 정상적인 동작입니다.
