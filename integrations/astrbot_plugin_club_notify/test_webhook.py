import importlib.util
import tempfile
import unittest
import uuid
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

spec = importlib.util.spec_from_file_location(
    "club_webhook", Path(__file__).with_name("webhook.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Webhook = module.Webhook
TOKEN = "t" * 32


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.fail_target = None

        async def send(target, text):
            if target == self.fail_target:
                return False
            self.sent.append((target, text))
            return True

        self.hook = Webhook(
            TOKEN,
            {"1": {"*": ["qq:GroupMessage:123", "qq:FriendMessage:456"]}},
            Path(self.tmp.name) / "receipts.db",
            send,
        )
        self.client = TestClient(TestServer(self.hook.app()))
        await self.client.start_server()
        self.payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "weekly_unqualified",
            "team_id": 1,
            "text": "本周尚未达标的同学有：\n小明",
        }

    async def asyncTearDown(self):
        await self.client.close()
        self.hook.close()
        self.tmp.cleanup()

    async def post(self, data=None, token=TOKEN):
        return await self.client.post(
            "/club/notify",
            json=self.payload if data is None else data,
            headers={"Authorization": "Bearer " + token},
        )

    async def test_authentication_and_validated_body(self):
        self.assertEqual((await self.post(token="wrong")).status, 401)
        for payload in [
            [],
            {},
            {**self.payload, "event_type": []},
            {**self.payload, "team_id": True},
            {**self.payload, "event_id": "invalid"},
            {**self.payload, "text": ""},
        ]:
            self.assertEqual((await self.post(payload)).status, 400)
        self.assertEqual(self.sent, [])

    async def test_delivers_group_and_private_and_deduplicates(self):
        for _ in range(2):
            response = await self.post()
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["ok"])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(
            {t for t, _ in self.sent}, {"qq:GroupMessage:123", "qq:FriendMessage:456"}
        )

    async def test_partial_failure_retries_only_failed_destination(self):
        self.fail_target = "qq:FriendMessage:456"
        self.assertEqual((await self.post()).status, 503)
        self.assertEqual(len(self.sent), 1)
        self.fail_target = None
        self.assertEqual((await self.post()).status, 200)
        self.assertEqual(len(self.sent), 2)

    async def test_event_id_conflict(self):
        await self.post()
        self.assertEqual(
            (await self.post({**self.payload, "text": "changed"})).status, 409
        )

    async def test_team_specific_key_cannot_impersonate_another_team(self):
        self.hook.team_tokens = {"1": "a" * 64, "2": "b" * 64}
        self.hook.routes["2"] = {"*": ["qq:GroupMessage:222"]}
        self.assertEqual((await self.post(token=TOKEN)).status, 401)
        self.assertEqual((await self.post(token="b" * 64)).status, 401)
        self.assertEqual((await self.post(token="a" * 64)).status, 200)
        self.assertEqual(
            (await self.post({**self.payload, "team_id": 2}, token="a" * 64)).status,
            401,
        )
        self.assertEqual(
            (
                await self.post(
                    {**self.payload, "event_id": str(uuid.uuid4()), "team_id": 2},
                    token="b" * 64,
                )
            ).status,
            200,
        )

    async def test_missing_route_cannot_override_target_via_payload(self):
        self.assertEqual(
            (
                await self.post(
                    {**self.payload, "team_id": 2, "targets": ["qq:GroupMessage:999"]}
                )
            ).status,
            422,
        )
        self.assertEqual(self.sent, [])

    async def test_long_message_segments_and_receipts_survive_restart(self):
        self.payload["text"] = "长" * 3200
        self.assertEqual((await self.post()).status, 200)
        self.assertEqual(len(self.sent), 6)
        await self.client.close()
        self.hook.close()
        self.hook = Webhook(
            TOKEN, self.hook.routes, Path(self.tmp.name) / "receipts.db", self.hook.send
        )
        self.client = TestClient(TestServer(self.hook.app()))
        await self.client.start_server()
        self.assertEqual((await self.post()).status, 200)
        self.assertEqual(len(self.sent), 6)


if __name__ == "__main__":
    unittest.main()
