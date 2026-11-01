import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "club_notification_config", Path(__file__).with_name("notification_config.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConfigurationTests(unittest.TestCase):
    def test_team_tokens_accept_independent_keys_and_reject_bad_entries(self):
        self.assertEqual(
            module.parse_team_tokens(
                {"team_api_tokens": ["1=" + "a" * 64, "2=" + "b" * 64]}
            ),
            {"1": "a" * 64, "2": "b" * 64},
        )
        for entries in [
            ["1=short"],
            ["abc=" + "a" * 64],
            ["1=" + "a" * 64, "1=" + "b" * 64],
            ["1=" + "a" * 31 + " "],
        ]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                module.parse_team_tokens({"team_api_tokens": entries})

    def test_visual_sections_route_events_independently(self):
        routes = module.build_routes(
            {
                "platform_id": "napcat",
                "interaction_notifications": {
                    "group_qqs": ["123", "456", "123"],
                    "user_qqs": ["789"],
                },
                "qualification_notifications": {
                    "group_qqs": ["987"],
                    "user_qqs": ["654", "321"],
                },
            }
        )["*"]
        for event in module.INTERACTION_EVENTS:
            self.assertEqual(
                routes[event],
                [
                    "napcat:GroupMessage:123",
                    "napcat:GroupMessage:456",
                    "napcat:FriendMessage:789",
                ],
            )
        self.assertEqual(
            routes["weekly_unqualified"],
            [
                "napcat:GroupMessage:987",
                "napcat:FriendMessage:654",
                "napcat:FriendMessage:321",
            ],
        )

    def test_only_one_category_can_be_configured(self):
        routes = module.build_routes(
            {"qualification_notifications": {"user_qqs": [123]}}
        )
        self.assertEqual(set(routes["*"]), {"weekly_unqualified"})
        self.assertEqual(
            routes["*"]["weekly_unqualified"],
            [module.AUTO_QQ_PLATFORM + ":FriendMessage:123"],
        )

    def test_bad_numbers_do_not_start_a_partially_configured_route(self):
        for value in [["123,456"], ["abc"], ["0"], [True], "123"]:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                module.build_routes({"interaction_notifications": {"group_qqs": value}})

    def test_old_routes_are_retained_and_visual_category_takes_precedence(self):
        old = {"1": {"*": ["old:GroupMessage:123"]}}
        self.assertEqual(module.build_routes({"routes": json.dumps(old)}), old)
        routes = module.build_routes(
            {
                "routes": json.dumps(old),
                "platform_id": "new",
                "interaction_notifications": {"user_qqs": ["456"]},
            }
        )
        self.assertEqual(routes["1"]["submission_report"], ["new:FriendMessage:456"])
        self.assertEqual(routes["1"]["*"], ["old:GroupMessage:123"])

    def test_auto_detects_unique_onebot_and_rejects_ambiguous_connections(self):
        platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp", id="napcat")
        )
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[platform])
        )
        target = module.AUTO_QQ_PLATFORM + ":GroupMessage:123"
        self.assertEqual(
            module.resolve_qq_target(context, target), "napcat:GroupMessage:123"
        )
        self.assertEqual(
            module.resolve_qq_target(context, "manual:FriendMessage:456"),
            "manual:FriendMessage:456",
        )
        for platforms in [[], [platform, platform]]:
            context.platform_manager.platform_insts = platforms
            with self.assertRaises(ValueError):
                module.resolve_qq_target(context, target)

    def test_visual_schema_has_both_recipient_lists(self):
        schema = json.loads(
            Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8")
        )
        self.assertTrue(schema["api_token"]["secret"])
        for field in ["interaction_notifications", "qualification_notifications"]:
            self.assertEqual(schema[field]["type"], "object")
            for key in ["group_qqs", "user_qqs"]:
                self.assertEqual(schema[field]["items"][key]["type"], "list")
                self.assertEqual(schema[field]["items"][key]["default"], [])
