"""Platform rewards using the existing beginner/experienced classification."""

from copy import deepcopy

from .grades import grade_on
from .models import UserProfile, default_problem_rewards

PLATFORMS = {
    "codeforces": "Codeforces",
    "atcoder": "AtCoder",
    "nowcoder": "牛客",
    "luogu": "洛谷",
    "leetcode": "LeetCode",
    "qoj": "QOJ",
}


def reward_groups():
    return {"beginner": "小登", "experienced": "老登"}


def group_for(user, day=None, profile=None):
    profile = profile or UserProfile.objects.filter(user=user).first()
    level = grade_on(profile, day)
    if level is None:
        return None
    return "beginner" if level == 0 else "experienced"


def settings_data(team):
    defaults = team.problem_rewards or default_problem_rewards()
    result = {}
    for group in reward_groups():
        fallback = {
            "contest_rewards": {
                p: ([2, 2, 2] if p == "qoj" else list(defaults.get(group, [1, 2, 3])))
                for p in PLATFORMS
            },
            "practice_rewards": {
                p: ([2, 2, 2] if p == "qoj" else [0, 0, 0]) for p in PLATFORMS
            },
        }
        configured = (team.reward_settings or {}).get(group, {})
        fallback.update(deepcopy(configured))
        result[group] = fallback
    return result


def validate_settings(value):
    if not isinstance(value, dict) or not set(value) <= set(reward_groups()):
        raise ValueError("奖励身份仅支持小登或老登")

    def amount(n):
        if type(n) is not int or not 0 <= n <= 100000:
            raise ValueError("奖励需为 0–100000 的整数")

    for config in value.values():
        if not isinstance(config, dict) or set(config) != {
            "contest_rewards",
            "practice_rewards",
        }:
            raise ValueError("奖励配置格式不正确")
        for field in ("contest_rewards", "practice_rewards"):
            rows = config[field]
            if not isinstance(rows, dict) or set(rows) != set(PLATFORMS):
                raise ValueError("请配置各平台奖励")
            for platform, values in rows.items():
                if not isinstance(values, list) or len(values) != 3:
                    raise ValueError("题目奖励需填写三档")
                for number in values:
                    amount(number)
                if platform == "qoj" and len(set(values)) != 1:
                    raise ValueError("QOJ 使用统一单题奖励")


def amount_for(
    team, platform, group, difficulty=None, source="contest", day=None, level=None
):
    from .contest_rewards import THRESHOLDS, difficulty_value

    if (
        group not in reward_groups()
        or platform not in PLATFORMS
        or source not in ("contest", "practice")
    ):
        return 0
    config = (team.reward_settings or {}).get(group, {})
    rows = config.get(source + "_rewards", {})
    fallback = (
        [2, 2, 2]
        if platform == "qoj"
        else [0, 0, 0]
        if source == "practice"
        else (team.problem_rewards or default_problem_rewards()).get(group, [1, 2, 3])
    )
    values = rows.get(platform, fallback)
    if (
        not isinstance(values, list)
        or len(values) != 3
        or any(type(n) is not int or not 0 <= n <= 100000 for n in values)
    ):
        return 0
    if platform == "qoj":
        return values[0]
    low, high = THRESHOLDS[platform][group]
    value = difficulty_value(difficulty)
    index = 0 if value is None or value < low else 1 if value < high else 2
    return values[index]


def daily_amount(member, day):
    return member.team.daily_checkin_reward


def streak_rules(member, day):
    return list(member.team.streak_rewards.order_by("days"))
