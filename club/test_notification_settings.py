import json
from datetime import datetime, time, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .astrbot_notifications import (
    deliver_pending,
    queue_notification,
    queue_weekly_reminders,
)
from .models import (
    AstrBotNotification,
    Semester,
    Team,
    TeamMembership,
    TeamNotificationSettings,
)
from .problem_parser import key_cipher


@override_settings(ASTRBOT_NOTIFY_URL="https://bot.example/club/notify")
class NotificationSettingsTests(TestCase):
    def setUp(self):
        self.root = User.objects.get(username="admin")
        self.team = Team.objects.create(
            name="Notify", code="SETTINGS-N", created_by=self.root
        )
        self.other = Team.objects.create(
            name="Other", code="SETTINGS-O", created_by=self.root
        )
        self.user = User.objects.create_user("notify-manager")
        TeamMembership.objects.create(
            team=self.team, user=self.user, display_name="小明", role="admin"
        )
        self.url = f"/api/management/teams/{self.team.pk}/notifications"
        self.client.force_login(self.root)

    def save(self, data):
        return self.client.put(
            self.url, json.dumps(data), content_type="application/json"
        )

    def enable(self, **extra):
        response = self.save({"enabled": True, "api_token": "a" * 64, **extra})
        self.assertEqual(response.status_code, 200, response.content)
        return TeamNotificationSettings.objects.get(team=self.team)

    def test_default_disabled_and_root_only(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["enabled"])
        self.assertEqual(response.json()["review_weekday"], 6)
        self.assertEqual(response.json()["review_time"], "21:00")
        self.assertFalse(TeamNotificationSettings.objects.exists())
        self.assertIsNone(
            queue_notification("disabled", "item_used", self.team, "test")
        )
        self.client.force_login(self.user)
        for method in ["get", "put", "post"]:
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 403)

    def test_generation_not_saved_and_token_encrypted_not_echoed(self):
        response = self.client.post(
            self.url,
            json.dumps({"action": "generate_token"}),
            content_type="application/json",
        )
        token = response.json()["api_token"]
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertFalse(TeamNotificationSettings.objects.exists())
        self.assertEqual(self.save({"api_token": token}).status_code, 200)
        config = TeamNotificationSettings.objects.get(team=self.team)
        self.assertNotEqual(config.encrypted_api_token, token)
        self.assertEqual(
            key_cipher().decrypt(config.encrypted_api_token.encode()).decode(), token
        )
        data = self.client.get(self.url).json()
        self.assertTrue(data["has_api_token"])
        self.assertNotIn("api_token", data)
        self.assertNotIn(token, str(data))
        self.assertFalse(config.enabled)

    def test_validation_is_atomic_and_does_not_change_other_team(self):
        self.enable()
        for data in [
            {"enabled": "true"},
            {"report_enabled": 1},
            {"review_weekday": True},
            {"review_weekday": 7},
            {"review_time": "24:00"},
            {"review_time": "12:60"},
            {"api_token": "short"},
            {"api_token": "a" * 31 + " "},
        ]:
            with self.subTest(data=data):
                self.assertEqual(
                    self.save({**data, "physical_purchase_enabled": False}).status_code,
                    400,
                )
                self.assertTrue(
                    TeamNotificationSettings.objects.get(
                        team=self.team
                    ).physical_purchase_enabled
                )
        self.assertFalse(
            TeamNotificationSettings.objects.filter(team=self.other).exists()
        )

    def test_enable_requires_key_and_blank_preserves_saved_key(self):
        self.assertEqual(self.save({"enabled": True}).status_code, 400)
        self.assertFalse(TeamNotificationSettings.objects.exists())
        config = self.enable()
        self.assertEqual(
            self.save({"api_token": "", "report_enabled": False}).status_code, 200
        )
        config.refresh_from_db()
        self.assertEqual(
            key_cipher().decrypt(config.encrypted_api_token.encode()).decode(), "a" * 64
        )
        self.assertFalse(config.report_enabled)

    @patch("club.astrbot_notifications.requests.post")
    def test_independent_flags_team_key_and_pending_cancellation(self, post):
        self.enable(report_enabled=False)
        self.assertIsNone(
            queue_notification("report", "submission_report", self.team, "report")
        )
        self.assertIsNotNone(queue_notification("use", "item_used", self.team, "use"))
        self.assertIsNone(queue_notification("other", "item_used", self.other, "use"))
        post.return_value = Mock(status_code=200, json=lambda: {"ok": True})
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer " + "a" * 64
        )
        row = queue_notification("purchase", "physical_purchase", self.team, "purchase")
        self.assertEqual(
            self.save({"physical_purchase_enabled": False}).status_code, 200
        )
        row.refresh_from_db()
        self.assertIsNotNone(row.cancelled_at)
        self.assertEqual(deliver_pending(), 0)
        row = queue_notification("pending-use", "item_used", self.team, "use")
        self.assertEqual(self.save({"enabled": False}).status_code, 200)
        row.refresh_from_db()
        self.assertIsNotNone(row.cancelled_at)

    def test_configured_weekday_and_time_and_once_per_training_week(self):
        config = self.enable(review_weekday=2, review_time="14:30")
        now = datetime(2026, 11, 4, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        Semester.objects.create(
            team=self.team,
            name="Fall",
            starts_on=now.date() - timedelta(days=2),
            ends_on=now.date() + timedelta(days=20),
        )
        self.assertEqual(queue_weekly_reminders(now - timedelta(days=1)), 0)
        self.assertEqual(queue_weekly_reminders(now - timedelta(minutes=1)), 0)
        self.assertEqual(queue_weekly_reminders(now), 1)
        self.assertEqual(queue_weekly_reminders(now + timedelta(minutes=1)), 0)
        config.review_weekday = 3
        config.review_time = time(12, 0)
        config.save()
        self.assertEqual(queue_weekly_reminders(now + timedelta(days=1)), 0)
        config.review_weekday = 2
        config.save()
        self.assertEqual(queue_weekly_reminders(now + timedelta(days=7)), 1)
        self.assertEqual(AstrBotNotification.objects.count(), 2)

    def test_disabled_qualification_does_not_queue_weekly(self):
        self.enable(qualification_enabled=False)
        now = datetime(2026, 11, 8, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
        Semester.objects.create(
            team=self.team,
            name="Fall",
            starts_on=now.date() - timedelta(days=6),
            ends_on=now.date(),
        )
        self.assertEqual(queue_weekly_reminders(now), 0)
