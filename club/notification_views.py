"""Administrator-only team notification settings; tokens are never echoed on GET."""

import re
import secrets
from datetime import time

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .management_views import administrator_required
from .models import AstrBotNotification, Team, TeamNotificationSettings
from .notification_config import EVENT_FLAGS
from .problem_parser import key_cipher
from .views import api_error, body

BOOLEAN_FIELDS = ["enabled", *EVENT_FLAGS.values()]


def configuration_data(config):
    return {
        **{field: getattr(config, field) for field in BOOLEAN_FIELDS},
        "has_api_token": bool(config.encrypted_api_token),
        "review_weekday": config.review_weekday,
        "review_time": config.review_time.strftime("%H:%M"),
        "endpoint_configured": bool(settings.ASTRBOT_NOTIFY_URL),
        "timezone": "Asia/Shanghai",
    }


@administrator_required
@transaction.atomic
def notification_settings(request, team_id):
    team = Team.objects.select_for_update().filter(pk=team_id).first()
    if team is None:
        return api_error("团队不存在", 404)
    if request.method not in ("GET", "PUT", "POST"):
        return api_error("仅支持 GET、PUT 或 POST", 405)
    config = TeamNotificationSettings.objects.filter(
        team=team
    ).first() or TeamNotificationSettings(team=team)
    if request.method == "GET":
        response = JsonResponse(configuration_data(config))
    elif request.method == "POST":
        data = body(request)
        if not isinstance(data, dict) or data.get("action") != "generate_token":
            return api_error("操作无效")
        response = JsonResponse({"api_token": secrets.token_urlsafe(48)})
    else:
        data = body(request)
        if not isinstance(data, dict):
            return api_error("通知配置格式无效")
        for field in BOOLEAN_FIELDS:
            if field in data:
                if type(data[field]) is not bool:
                    return api_error("通知开关必须为开启或关闭")
                setattr(config, field, data[field])
        if "review_weekday" in data:
            if (
                type(data["review_weekday"]) is not int
                or not 0 <= data["review_weekday"] <= 6
            ):
                return api_error("请选择周一至周日")
            config.review_weekday = data["review_weekday"]
        if "review_time" in data:
            value = data["review_time"]
            if not isinstance(value, str) or not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", value
            ):
                return api_error("请选择有效的审核时间")
            config.review_time = time.fromisoformat(value)
        if "api_token" in data:
            token = data["api_token"]
            if not isinstance(token, str) or (
                token
                and (not 32 <= len(token) <= 4096 or any(c.isspace() for c in token))
            ):
                return api_error("api_token 需为 32 至 4096 位，且不能包含空白字符")
            if token:
                config.encrypted_api_token = (
                    key_cipher().encrypt(token.encode()).decode()
                )
        if config.enabled and not config.encrypted_api_token:
            return api_error("请先生成或填写并保存 api_token 密钥")
        config.save()
        # Explicitly disabling a notification cancels queued deliveries too.
        pending = AstrBotNotification.objects.filter(
            payload__team_id=team_id,
            delivered_at__isnull=True,
            cancelled_at__isnull=True,
        )
        for event, flag in EVENT_FLAGS.items():
            if not config.enabled or not getattr(config, flag):
                pending.filter(payload__event_type=event).update(
                    cancelled_at=timezone.now(), last_error="通知已关闭，取消发送"
                )
        response = JsonResponse({"ok": True, **configuration_data(config)})
    response["Cache-Control"] = "private, no-store"
    return response
