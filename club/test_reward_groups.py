import json
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from .contest_rewards import REWARD_START, reward_for
from .models import (
    ContestParticipation,
    ContestProblemProgress,
    PlatformBinding,
    PlatformSolvedRecord,
    StreakReward,
    Team,
    TeamMembership,
    UserProfile,
)
from .platform_rewards import available_rewards
from .reward_rules import (
    amount_for,
    daily_amount,
    group_for,
    reward_groups,
    settings_data,
    streak_rules,
    validate_settings,
)


class RewardGroupBoundaryTests(SimpleTestCase):
    def test_first_autumn_and_promotion_use_existing_identity(self):
        profile = SimpleNamespace(admission_year=2026)
        for day, expected in [
            (date(2026, 8, 1), "beginner"),
            (date(2027, 1, 31), "beginner"),
            (date(2027, 2, 14), "beginner"),
            (date(2027, 2, 15), "experienced"),
            (date(2028, 11, 1), "experienced"),
        ]:
            self.assertEqual(group_for(None, day, profile), expected)
        self.assertIsNone(group_for(None, date(2026, 7, 31), profile))
        for year in [2013, 2024, 2025]:
            self.assertEqual(
                group_for(
                    None, date(2026, 11, 1), SimpleNamespace(admission_year=year)
                ),
                "experienced",
            )
        self.assertEqual(reward_groups(), {"beginner": "小登", "experienced": "老登"})


class RewardGroupTests(TestCase):
    def setUp(self):
        self.root = User.objects.get(username="admin")
        self.team = Team.objects.create(
            name="Rewards",
            code="REWARD-GROUPS",
            created_by=self.root,
            currency_enabled=True,
            daily_checkin_reward=4,
            problem_rewards={"beginner": [4, 5, 6], "experienced": [7, 8, 9]},
        )
        self.user = User.objects.create_user("reward-groups")
        self.member = TeamMembership.objects.create(
            team=self.team, user=self.user, display_name="Member"
        )
        self.profile = UserProfile.objects.create(user=self.user, admission_year=2026)
        StreakReward.objects.create(team=self.team, days=3, reward=2)

    def test_old_amounts_and_all_platform_defaults_are_retained(self):
        config = settings_data(self.team)
        self.assertEqual(set(config), {"beginner", "experienced"})
        for group, expected in [("beginner", [4, 5, 6]), ("experienced", [7, 8, 9])]:
            self.assertEqual(
                set(config[group]), {"contest_rewards", "practice_rewards"}
            )
            for platform in ["codeforces", "atcoder", "nowcoder", "luogu", "leetcode"]:
                self.assertEqual(config[group]["contest_rewards"][platform], expected)
                self.assertEqual(config[group]["practice_rewards"][platform], [0, 0, 0])
            self.assertEqual(config[group]["contest_rewards"]["qoj"], [2, 2, 2])
            self.assertEqual(config[group]["practice_rewards"]["qoj"], [2, 2, 2])
        validate_settings(config)

    def test_cohort_keys_and_per_identity_signin_overrides_are_rejected(self):
        config = settings_data(self.team)
        with self.assertRaises(ValueError):
            validate_settings({"2026": config["beginner"]})
        bad = deepcopy(config)
        bad["beginner"]["daily_checkin_reward"] = 99
        with self.assertRaises(ValueError):
            validate_settings(bad)

    def test_shared_signin_and_streak_ignore_cohort_overrides(self):
        self.team.reward_settings = {
            "2026": {
                "daily_checkin_reward": 99,
                "streak_rewards": [{"days": 3, "reward": 99}],
            }
        }
        for day in [date(2026, 11, 1), date(2027, 3, 1)]:
            self.assertEqual(daily_amount(self.member, day), 4)
            self.assertEqual(
                [(r.days, r.reward) for r in streak_rules(self.member, day)], [(3, 2)]
            )

    def test_group_and_platform_and_source_select_independent_amounts(self):
        self.team.reward_settings = settings_data(self.team)
        self.team.reward_settings["beginner"]["contest_rewards"]["codeforces"] = [
            10,
            20,
            30,
        ]
        self.team.reward_settings["beginner"]["practice_rewards"]["codeforces"] = [
            1,
            2,
            3,
        ]
        self.assertEqual(amount_for(self.team, "codeforces", "beginner", 999), 10)
        self.assertEqual(amount_for(self.team, "codeforces", "beginner", 1000), 20)
        self.assertEqual(amount_for(self.team, "codeforces", "beginner", 1600), 30)
        self.assertEqual(amount_for(self.team, "codeforces", "experienced", 1000), 7)
        self.assertEqual(
            amount_for(self.team, "codeforces", "beginner", 1000, "practice"), 2
        )
        self.assertEqual(amount_for(self.team, "atcoder", "beginner", 400), 5)
        self.assertEqual(
            amount_for(self.team, "qoj", "experienced", 9999, "practice"), 2
        )
        self.assertEqual(amount_for(self.team, "codeforces", "2026", 9999), 0)

    def test_old_contest_keeps_beginner_identity_after_promotion(self):
        contest = ContestParticipation.objects.create(
            user=self.user,
            platform="leetcode",
            account="cn:member",
            contest_key="test",
            title="Test",
            url="https://leetcode.cn/contest/test/",
            starts_at=REWARD_START,
            ends_at=REWARD_START + timedelta(hours=2),
            detail_complete=True,
        )
        problem = ContestProblemProgress.objects.create(
            participation=contest,
            problem_key="sample",
            label="Q1",
            title="Sample",
            difficulty=600,
            accepted=True,
            official_ac=True,
            accepted_at=REWARD_START + timedelta(minutes=1),
        )
        with patch(
            "django.utils.timezone.now", return_value=REWARD_START + timedelta(days=150)
        ):
            self.assertEqual(reward_for(problem, self.member)[:2], (5, "beginner"))

    def test_practice_uses_solve_date_and_falls_back_to_current_identity(self):
        config = settings_data(self.team)
        config["beginner"]["practice_rewards"]["qoj"] = [4, 4, 4]
        config["experienced"]["practice_rewards"]["qoj"] = [9, 9, 9]
        self.team.reward_settings = config
        self.team.save()
        binding = PlatformBinding.objects.create(
            user=self.user, platform="qoj", account="member"
        )
        PlatformSolvedRecord.objects.create(
            binding=binding, problem_key="100", solved_on=date(2026, 11, 1)
        )
        PlatformSolvedRecord.objects.create(
            binding=binding, problem_key="101", solved_on=None
        )
        with patch(
            "django.utils.timezone.now", return_value=REWARD_START + timedelta(days=150)
        ):
            result = available_rewards(self.member)
        self.assertEqual(
            (result["qoj|100"]["amount"], result["qoj|100"]["group"]), (4, "beginner")
        )
        self.assertEqual(
            (result["qoj|101"]["amount"], result["qoj|101"]["group"]),
            (9, "experienced"),
        )

    def test_admin_api_keeps_signin_rules_shared_and_roundtrips_two_groups(self):
        self.client.force_login(self.root)
        config = settings_data(self.team)
        config["experienced"]["practice_rewards"]["atcoder"] = [3, 4, 5]
        response = self.client.post(
            "/api/currency/admin/config",
            json.dumps(
                {
                    "team_id": self.team.pk,
                    "reward_settings": config,
                    "daily_checkin_reward": 6,
                    "streak_rewards": [{"days": 3, "reward": 8}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.team.refresh_from_db()
        self.assertEqual(self.team.daily_checkin_reward, 6)
        self.assertEqual(
            list(self.team.streak_rewards.values_list("days", "reward")), [(3, 8)]
        )
        self.assertEqual(self.team.reward_settings, config)
        self.assertEqual(
            self.team.problem_rewards, {"beginner": [4, 5, 6], "experienced": [7, 8, 9]}
        )
