"""Exercise real plugin initialization with only the AstrBot framework stubbed."""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        api = types.ModuleType("astrbot.api")
        api.AstrBotConfig = dict
        api.logger = Mock()
        event = types.ModuleType("astrbot.api.event")
        event.AstrMessageEvent = object
        event.MessageChain = Mock()
        event.filter = types.SimpleNamespace(
            permission_type=lambda *a: lambda f: f,
            command=lambda *a: lambda f: f,
            PermissionType=types.SimpleNamespace(ADMIN="admin"),
        )
        star = types.ModuleType("astrbot.api.star")
        star.Context = object
        star.Star = type(
            "Star",
            (),
            {"__init__": lambda self, context: setattr(self, "context", context)},
        )
        star.StarTools = types.SimpleNamespace(
            get_data_dir=lambda name: Path(self.tmp.name)
        )
        star.register = lambda *a: lambda cls: cls
        self.modules = patch.dict(
            sys.modules,
            {
                "astrbot": types.ModuleType("astrbot"),
                "astrbot.api": api,
                "astrbot.api.event": event,
                "astrbot.api.star": star,
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        folder = Path(__file__).parent
        spec = importlib.util.spec_from_file_location(
            "club_notify_lifecycle_test",
            folder / "main.py",
            submodule_search_locations=[str(folder)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.plugin_class = module.ClubNotify
        self.logger = api.logger

    async def test_unconfigured_or_invalid_plugin_loads_without_listener(self):
        for config in [
            {},
            {"api_token": "short"},
            {"api_token": "x" * 32},
            {"api_token": "x" * 32, "routes": "invalid json"},
            {"api_token": "x" * 32, "routes": '{"1":{"*":[]}}'},
        ]:
            with self.subTest(config=config):
                plugin = self.plugin_class(object(), config)
                await plugin.initialize()
                self.assertIsNone(plugin.runner)
                self.assertIsNone(plugin.webhook)
                event = types.SimpleNamespace(
                    unified_msg_origin="qq:GroupMessage:123",
                    plain_result=lambda text: text,
                )
                self.assertEqual(
                    [result async for result in plugin.session(event)],
                    ["qq:GroupMessage:123"],
                )
                await plugin.terminate()
        self.assertEqual(self.logger.warning.call_count, 5)

    async def test_configured_plugin_starts_and_releases_listener(self):
        plugin = self.plugin_class(
            object(),
            {
                "api_token": "x" * 32,
                "host": "127.0.0.1",
                "port": 0,
                "routes": json.dumps({"1": {"*": ["qq:GroupMessage:123"]}}),
            },
        )
        try:
            await plugin.initialize()
            self.assertIsNotNone(plugin.runner)
            self.assertTrue(plugin.runner.addresses)
            self.assertIsNotNone(plugin.webhook)
        finally:
            await plugin.terminate()
        self.assertIsNone(plugin.runner)
        self.assertIsNone(plugin.webhook)

    async def test_visual_configuration_starts_with_only_weekly_recipients(self):
        context = types.SimpleNamespace(
            send_message=AsyncMock(return_value=True),
            platform_manager=types.SimpleNamespace(
                platform_insts=[
                    types.SimpleNamespace(
                        meta=lambda: types.SimpleNamespace(
                            name="aiocqhttp", id="napcat"
                        )
                    )
                ]
            ),
        )
        plugin = self.plugin_class(
            context,
            {
                "api_token": "x" * 32,
                "host": "127.0.0.1",
                "port": 0,
                "qualification_notifications": {
                    "group_qqs": ["123456"],
                    "user_qqs": ["654321"],
                },
            },
        )
        try:
            await plugin.initialize()
            self.assertIsNotNone(plugin.runner)
            self.assertEqual(len(plugin.webhook.routes["*"]["weekly_unqualified"]), 2)
            self.assertNotIn("submission_report", plugin.webhook.routes["*"])
            port = plugin.runner.addresses[0][1]
            async with (
                ClientSession() as client,
                client.post(
                    f"http://127.0.0.1:{port}/club/notify",
                    headers={"Authorization": "Bearer " + "x" * 32},
                    json={
                        "event_id": str(uuid.uuid4()),
                        "event_type": "weekly_unqualified",
                        "team_id": 1,
                        "text": "本周尚未达标的同学有：测试",
                    },
                ) as response,
            ):
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["ok"])
            self.assertEqual(
                [call.args[0] for call in context.send_message.await_args_list],
                ["napcat:GroupMessage:123456", "napcat:FriendMessage:654321"],
            )
        finally:
            await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
