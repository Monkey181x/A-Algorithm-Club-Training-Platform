"""Request-scoped reward identities and claims; never cached across requests."""
from .ac_evidence import metadata, canonical
from .models import PlatformProblemClaim, ContestProblemClaim


class RewardSnapshot:
    def __init__(self, member):
        self.member = member
        self.maps = {}
        self.indexes = {}
        self.new_claims = set()
        self.platform_claims = list(PlatformProblemClaim.objects.filter(team=member.team).values(
            'user_id', 'account', 'platform', 'problem_key'))
        legacy = ContestProblemClaim.objects.filter(team=member.team).values(
            'user_id', 'member_id', 'platform', 'account', 'contest_key', 'problem_key')
        self.own_legacy = set()
        self.account_legacy = set()
        for row in legacy:
            if row['platform'] == 'codeforces':
                # Pre-ledger claims can carry the other division's local problem key.
                self.platform_claims.append({
                    'platform': 'codeforces', 'account': row['account'],
                    'user_id': member.user_id if row['member_id'] == member.pk else row['user_id'],
                    'problem_key': f"{row['contest_key']}:{row['problem_key']}",
                })
            key = (row['platform'], row['contest_key'], row['problem_key'])
            if row['user_id'] == member.user_id or row['member_id'] == member.pk:
                self.own_legacy.add(key)
            self.account_legacy.add((row['account'].casefold(), *key))

    def info(self, platform):
        if platform not in self.maps:
            self.maps[platform] = metadata(platform)
        return self.maps[platform]

    def identity(self, problem):
        from .platform_rewards import contest_identity
        return contest_identity(problem, self.info(problem.participation.platform))

    def reward_for(self, problem, profile, difficulty_info):
        from .ac_evidence import reward_pending
        from .contest_rewards import reward_for
        from .platform_solved import contest_key
        platform = problem.participation.platform
        amount, group, reason = reward_for(problem, self.member, profile, difficulty_info=difficulty_info)
        if amount and platform == 'codeforces' and reward_pending(platform, contest_key(problem), self.info(platform)):
            return 0, group, '题目核对中'
        return amount, group, reason

    def claimed(self, problem):
        c = problem.participation
        identity = self.identity(problem)
        if c.platform not in self.indexes:
            own, accounts = set(), set()
            for row in self.platform_claims:
                base = 'leetcode' if row['platform'].startswith('leetcode_') else row['platform']
                if base != c.platform:continue
                key = (row['platform'], canonical(base,row['problem_key'],self.info(base)))
                if row['user_id'] == self.member.user_id:own.add(key)
                accounts.add((row['account'].casefold(), *key))
            self.indexes[c.platform] = own, accounts
        own, accounts = self.indexes[c.platform]
        legacy = (c.platform,c.contest_key,problem.problem_key)
        return (identity in self.new_claims or identity in own or
                (c.account.casefold(), *identity) in accounts or legacy in self.own_legacy or
                (c.account.casefold(), *legacy) in self.account_legacy)
