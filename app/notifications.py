"""Firebase Cloud Messaging 발송 래퍼.

- 자격증명 경로(`Settings.firebase_credentials_path`)가 설정되어 있고 실제 파일이 존재할 때만 활성화.
- 미설정 시 모든 send 호출은 no-op로 폴백하고 WebSocket이 실시간 전파를 담당.
- 잘못된(만료/등록 해제된) 토큰은 DB에서 자동 정리.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.config import Settings

from app.models import UserPushToken

logger = logging.getLogger(__name__)


@dataclass
class FirebaseNotifier:
    """Firebase Admin SDK를 통한 멀티캐스트 발송기.

    `enabled=False`이면 send_to_tokens가 즉시 return하며 WebSocket 폴백 모드로 동작한다.
    """

    enabled: bool
    _initialized_once: bool = False

    @classmethod
    def from_settings(cls, settings: "Settings") -> "FirebaseNotifier":
        path = settings.firebase_credentials_path
        if not path:
            logger.info("FCM disabled: GYOUM_FIREBASE_CREDENTIALS_PATH 미설정. WebSocket 폴백만 동작합니다.")
            return cls(enabled=False)
        if not os.path.isfile(path):
            logger.warning(
                "FCM disabled: 자격증명 파일을 찾을 수 없습니다 (%s). WebSocket 폴백만 동작합니다.",
                path,
            )
            return cls(enabled=False)
        try:
            import firebase_admin
            from firebase_admin import credentials  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "FCM disabled: firebase-admin 패키지를 import할 수 없습니다. "
                "`uv sync` 후 재시작해주세요."
            )
            return cls(enabled=False)
        try:
            if not firebase_admin._apps:  # type: ignore[attr-defined]
                cred = credentials.Certificate(path)
                firebase_admin.initialize_app(cred)
            logger.info("FCM enabled: Firebase Admin SDK 초기화 완료 (%s).", path)
            return cls(enabled=True, _initialized_once=True)
        except (
            Exception
        ) as error:  # pragma: no cover — 잘못된 자격증명 진단용 로그
            logger.exception("FCM 초기화 실패. WebSocket 폴백으로 전환합니다. (%s)", error)
            return cls(enabled=False)

    def send_to_tokens(
        self,
        db: Session,
        tokens: list[str],
        *,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> None:
        if not self.enabled or not tokens:
            return
        from firebase_admin import messaging  # type: ignore[import-not-found]

        unique_tokens = list({token for token in tokens if token})
        if not unique_tokens:
            return

        message = messaging.MulticastMessage(
            tokens=unique_tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="support-request-updates",
                    sound="default",
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", content_available=True),
                ),
            ),
        )
        try:
            response = messaging.send_each_for_multicast(message)
        except Exception:  # pragma: no cover — 일시적 네트워크 실패
            logger.exception("FCM 발송 실패 (tokens=%d).", len(unique_tokens))
            return

        invalid_tokens: list[str] = []
        for idx, send_response in enumerate(response.responses):
            if send_response.success:
                continue
            error = send_response.exception
            error_code = getattr(error, "code", None) or getattr(error, "_code", None)
            if error_code in {"registration-token-not-registered", "invalid-argument"}:
                invalid_tokens.append(unique_tokens[idx])
            else:
                logger.warning("FCM 개별 발송 실패 (token=%s, error=%s).", unique_tokens[idx], error)

        if invalid_tokens:
            self._invalidate_tokens(db, invalid_tokens)

    @staticmethod
    def _invalidate_tokens(db: Session, tokens: list[str]) -> None:
        try:
            db.execute(delete(UserPushToken).where(UserPushToken.token.in_(tokens)))
            db.commit()
            logger.info("FCM 무효 토큰 %d개 정리.", len(tokens))
        except Exception:  # pragma: no cover
            db.rollback()
            logger.exception("FCM 무효 토큰 정리 실패.")


_notifier_singleton: FirebaseNotifier | None = None


def get_notifier(settings: "Settings") -> FirebaseNotifier:
    global _notifier_singleton
    if _notifier_singleton is None:
        _notifier_singleton = FirebaseNotifier.from_settings(settings)
    return _notifier_singleton


def reset_notifier_for_tests() -> None:
    """테스트/리로드 시 싱글톤 초기화용."""
    global _notifier_singleton
    _notifier_singleton = None
