# 히요리 팬아트 → 디스코드 서버 배너 (알림 배너와 완전 분리)

이 코드는 기존 `banner_manager.py`와 `discord_v2.py`를 수정하지 않습니다.
**서버 자체의 배너**를 Discord API의 `PATCH /guilds/{guild.id}`로 바꾸는 별도 작업입니다.

## 이미지 공급 경로

`fanart_library.py`가 생성하는
`$BOT_DATA_DIR/creator_media/fanart_index.json`에서 **hanayo** 항목만 읽습니다.
원본 이미지는 `creator_media/fanart/hanayo/`에 있어야 합니다.
`approved=true`, `permission_confirmed=true`, `#hiyoriart_`,
원본 SHA-256, 원본 경로 검사를 모두 통과해야 후보가 됩니다.

**주의:** X에서 수집만 된 `fanart_candidates`는 사용하지 않습니다.
작가가 서버 배너 사용을 허락한 이미지인지 별도 검토 후 승인해야 합니다.

- 16:9: 960×540 축소
- 16:10: 위아래 자르지 않고 흐린 좌우 배경으로 960×540 변환
- 기타 비율 / 너무 작은 이미지: 제외
- 직전 사용한 원본 SHA-256 재선택 방지
- 이미지가 없거나 변경 실패 시 현재 서버 배너 유지

## 미리보기 (안전: Discord 변경 없음)

```bash
python guild_server_banner.py --output /tmp/hanayo_preview.jpg
```

승인 작품이 하나도 없다면 `{"status":"no_approved_hanayo_art"}`를 출력합니다.
`--output`은 서버 배너를 변경하지 않습니다.

## 운영 환경

디스코드 서버가 배너 기능을 지원하고, 봇에 **Manage Server** 권한이 있어야 합니다.
서버 ID / 토큰은 소스 저장소나 채팅에 노출하지 마세요.
서버 전용 환경변수:

```dotenv
SERVER_BANNER_ENABLED=1
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...
BOT_DATA_DIR=/home/ubuntu/chzzk-bot
```

## 명시적 1회 적용 (실제 변경)

운영 서버의 승인 목록에 유효한 작품이 있고, 환경변수가 설정된 경우:

```bash
python guild_server_banner.py --apply-once
```

성공 시 `status=changed`, 실패 시 상태 코드 또는 오류가 기록됩니다.
수동 적용은 예약 슬롯을 소비하지 않습니다. 상태 파일:
`creator_media/guild_banner_rotation_state.json`.

## 자동 적용

`SERVER_BANNER_ENABLED=1`일 때만 메인 봇의 독립 스레드가
**매일 08:00 및 18:00 KST**에 변경을 시도합니다.
기능 비활성화 시 기존 알림 시스템은 그대로 작동합니다.

## 검증

```bash
python -m unittest -v test_guild_server_banner.py
```

이 테스트는 실제 디스코드 서버 API를 호출하지 않습니다.

## 배포 상태

이 문서와 코드는 검토용 브랜치에만 있습니다. 실서버 배포, 실제 봇 토큰,
서버 배너 변경 성공 여부는 별도로 확인해야 합니다.
