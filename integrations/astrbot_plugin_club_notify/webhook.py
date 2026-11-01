"""Authenticated webhook with durable, per-destination/segment delivery receipts."""

import asyncio
import hashlib
import hmac
import json
import sqlite3
import uuid

from aiohttp import web

EVENT_TYPES = {
    "submission_report",
    "item_used",
    "physical_purchase",
    "weekly_unqualified",
}


class Webhook:
    def __init__(self, token, routes, database, send, team_tokens=None):
        self.team_tokens = team_tokens or {}
        if (
            not isinstance(token, str)
            or (token and len(token) < 32)
            or (not token and not self.team_tokens)
        ):
            raise ValueError("请配置至少32位的 API token")
        if not isinstance(routes, dict) or not routes:
            raise ValueError("请在用户互动或达标审查中添加通知群或用户 QQ 号")
        for team, events in routes.items():
            if not isinstance(team, str) or not isinstance(events, dict):
                raise TypeError("routes 格式错误")
            for event, targets in events.items():
                if (
                    event not in EVENT_TYPES | {"*"}
                    or not isinstance(targets, list)
                    or not targets
                ):
                    raise ValueError("routes 事件或目标格式错误")
                if any(
                    not isinstance(t, str)
                    or len(t.split(":")) != 3
                    or not all(t.split(":"))
                    for t in targets
                ):
                    raise ValueError("请使用完整 unified_msg_origin 会话标识")
        self.token, self.routes, self.send = token, routes, send
        self.lock = asyncio.Lock()
        self.db = sqlite3.connect(database)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, digest TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS receipts (event TEXT, target TEXT, part INTEGER, "
            "PRIMARY KEY(event, target, part))"
        )
        self.db.commit()

    async def receive(self, request):
        authorization = request.headers.get("Authorization", "").encode()
        if not any(
            hmac.compare_digest(authorization, ("Bearer " + token).encode())
            for token in [self.token, *self.team_tokens.values()]
            if token
        ):
            return web.json_response({"ok": False}, status=401)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise TypeError()
            event_id = str(uuid.UUID(data.get("event_id", "")))
            event_type = data.get("event_type")
            if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
                raise ValueError()
            if type(data.get("team_id")) is not int or data["team_id"] <= 0:
                raise ValueError()
            expected = self.team_tokens.get(str(data["team_id"]), self.token)
            if not expected or not hmac.compare_digest(
                authorization, ("Bearer " + expected).encode()
            ):
                return web.json_response({"ok": False}, status=401)
            text = data.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 200000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            return web.json_response(
                {"ok": False, "error": "invalid payload"}, status=400
            )
        routes = self.routes.get(str(data["team_id"]), self.routes.get("*", {}))
        targets = list(dict.fromkeys(routes.get(event_type, routes.get("*", []))))
        if not targets:
            return web.json_response(
                {"ok": False, "error": "no configured destination"}, status=422
            )
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        parts = [text[i : i + 1500] for i in range(0, len(text), 1500)]
        async with self.lock:
            prior = self.db.execute(
                "SELECT digest FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if prior and prior[0] != digest:
                return web.json_response(
                    {"ok": False, "error": "event ID conflict"}, status=409
                )
            self.db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?)", (event_id, digest)
            )
            self.db.commit()
            try:
                for target in targets:
                    for part, message in enumerate(parts):
                        if self.db.execute(
                            "SELECT 1 FROM receipts WHERE event=? AND target=? AND part=?",
                            (event_id, target, part),
                        ).fetchone():
                            continue
                        # A timeout is retried by the platform; successful segments are remembered.
                        result = await asyncio.wait_for(
                            self.send(target, message), timeout=12
                        )
                        if result is False:
                            raise RuntimeError("platform unavailable")
                        self.db.execute(
                            "INSERT INTO receipts VALUES (?, ?, ?)",
                            (event_id, target, part),
                        )
                        self.db.commit()
            except Exception:  # noqa: BLE001 - Adapter failures must remain retryable, without exposing private errors.
                return web.json_response(
                    {"ok": False, "error": "delivery failed; retry same event"},
                    status=503,
                )
        return web.json_response({"ok": True, "event_id": event_id})

    def app(self):
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post("/club/notify", self.receive)
        return app

    def close(self):
        self.db.close()
