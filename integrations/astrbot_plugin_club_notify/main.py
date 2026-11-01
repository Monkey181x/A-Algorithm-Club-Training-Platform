from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .notification_config import build_routes, parse_team_tokens, resolve_qq_target
from .webhook import Webhook


@register("astrbot_plugin_club_notify", "Monkey", "算法社训练平台通知", "1.2.0")
class ClubNotify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runner = None
        self.webhook = None

    async def initialize(self):
        # AstrBot loads plugins before the user can fill in their configuration.
        # Keep commands available while leaving the API closed until configured.
        try:
            token = self.config.get("api_token", "")
            team_tokens = parse_team_tokens(self.config)
            if (
                not isinstance(token, str)
                or (token and len(token.strip()) < 32)
                or (not token.strip() and not team_tokens)
            ):
                raise ValueError("请配置至少32位的 API token")
            routes = build_routes(self.config)
            database = (
                StarTools.get_data_dir("astrbot_plugin_club_notify")
                / "delivery.sqlite3"
            )
            self.webhook = Webhook(
                token.strip(), routes, database, self.send, team_tokens=team_tokens
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "训练通知插件已加载，通知 API 尚未启用：%s。请保存插件配置后重载插件。",
                exc,
            )
            return
        self.runner = web.AppRunner(self.webhook.app(), shutdown_timeout=15)
        try:
            await self.runner.setup()
            await web.TCPSite(
                self.runner,
                self.config.get("host", "127.0.0.1"),
                int(self.config.get("port", 8099)),
            ).start()
        except Exception:
            await self.terminate()
            raise

    async def send(self, target, text):
        target = resolve_qq_target(self.context, target)
        return await self.context.send_message(target, MessageChain().message(text))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("训练通知会话")
    async def session(self, event: AstrMessageEvent):
        """管理员获取当前群聊/私聊的通知目标标识。"""
        yield event.plain_result(event.unified_msg_origin)

    async def terminate(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        if self.webhook:
            self.webhook.close()
            self.webhook = None
