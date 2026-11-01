"""Translate the visual QQ configuration to the webhook's destination routes."""

import json
import re

AUTO_QQ_PLATFORM = "__club_auto_qq__"
INTERACTION_EVENTS = ("submission_report", "item_used", "physical_purchase")


def parse_team_tokens(config):
    entries = config.get("team_api_tokens", [])
    if not isinstance(entries, list):
        raise TypeError("团队专属密钥需逐项添加")
    tokens = {}
    for entry in entries:
        if not isinstance(entry, str):
            raise TypeError("团队专属密钥格式为：团队ID=密钥")
        team_id, separator, token = entry.strip().partition("=")
        if (
            not separator
            or not re.fullmatch(r"[1-9][0-9]*", team_id)
            or not 32 <= len(token) <= 4096
            or any(c.isspace() for c in token)
        ):
            raise ValueError("团队专属密钥格式为：团队ID=至少32位密钥")
        if team_id in tokens:
            raise ValueError("同一团队只能配置一个专属密钥")
        tokens[team_id] = token
    return tokens


def build_routes(config):
    # Retain existing installations' routing until visual recipients are supplied.
    routes = json.loads(config.get("routes", "{}") or "{}")
    if not isinstance(routes, dict):
        raise TypeError("旧版 routes 配置必须是 JSON 对象")
    platform_id = config.get("platform_id", "")
    if not isinstance(platform_id, str) or ":" in platform_id:
        raise ValueError("QQ 机器人连接 ID 格式错误")
    platform_id = platform_id.strip() or AUTO_QQ_PLATFORM
    for field, label, events in (
        ("interaction_notifications", "用户互动", INTERACTION_EVENTS),
        ("qualification_notifications", "达标审查", ("weekly_unqualified",)),
    ):
        section = config.get(field, {})
        if not isinstance(section, dict):
            raise TypeError(f"{label}配置格式错误")
        targets = []
        for key, kind in (("group_qqs", "GroupMessage"), ("user_qqs", "FriendMessage")):
            numbers = section.get(key, [])
            if not isinstance(numbers, list):
                raise TypeError(f"{label}的 QQ 号必须逐项添加到列表")
            for number in numbers:
                if not isinstance(number, str) and type(number) is not int:
                    raise ValueError(f"{label}的 QQ 号必须为正整数")
                number = str(number).strip()
                if not re.fullmatch(r"[1-9][0-9]{0,19}", number):
                    raise ValueError(f"{label}的 QQ 号必须为正整数，每项填写一个号码")
                targets.append(f"{platform_id}:{kind}:{number}")
        if targets:
            targets = list(dict.fromkeys(targets))
            # New visual recipients take precedence over old per-team routes.
            routes.setdefault("*", {})
            for team_routes in routes.values():
                if not isinstance(team_routes, dict):
                    raise TypeError("旧版 routes 团队配置格式错误")
                for event in events:
                    team_routes[event] = targets
    return routes


def resolve_qq_target(context, target):
    if not target.startswith(AUTO_QQ_PLATFORM + ":"):
        return target
    platforms = [
        p.meta()
        for p in context.platform_manager.platform_insts
        if p.meta().name == "aiocqhttp"
    ]
    if len(platforms) != 1 or not platforms[0].id:
        raise ValueError("未找到唯一 QQ OneBot 连接，请填写 QQ 机器人连接 ID")
    return platforms[0].id + target[len(AUTO_QQ_PLATFORM) :]
