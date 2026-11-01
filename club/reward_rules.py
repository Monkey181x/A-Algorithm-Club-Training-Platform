"""Team reward settings, split by admission cohort and platform."""
from copy import deepcopy
from types import SimpleNamespace

from .models import UserProfile, default_problem_rewards
from .grades import grade_on, grade_for_year, academic_year, cohort_data

PLATFORMS = {'codeforces':'Codeforces','atcoder':'AtCoder','nowcoder':'牛客','luogu':'洛谷','leetcode':'LeetCode','qoj':'QOJ'}
def reward_groups():
    return {str(year):cohort_data(SimpleNamespace(admission_year=year))['label']
            for year in range(academic_year(),2012,-1)}


def group_for(user,day=None,profile=None):
    profile=profile or UserProfile.objects.filter(user=user).first()
    if grade_on(profile,day) is None:return None
    year=getattr(profile,'admission_year',None)
    if year is None:
        year=max(2013,academic_year(profile.grade_date)-profile.grade//2)
    return str(year)


def settings_data(team):
    defaults=team.problem_rewards or default_problem_rewards()
    streaks=list(team.streak_rewards.values('days','reward'))
    if not streaks and not team.currency_enabled:
        streaks=[{'days':3,'reward':1},{'days':7,'reward':2}]
    result={}
    for group in reward_groups():
        legacy_group='beginner' if grade_for_year(int(group))==0 else 'experienced'
        fallback={'daily_checkin_reward':team.daily_checkin_reward,'streak_rewards':deepcopy(streaks),
                  'contest_rewards':{p:([2,2,2] if p=='qoj' else list(defaults.get(legacy_group,[1,2,3]))) for p in PLATFORMS},
                  'practice_rewards':{p:([2,2,2] if p=='qoj' else [0,0,0]) for p in PLATFORMS}}
        configured=(team.reward_settings or {}).get(group,{})
        fallback.update(deepcopy(configured))
        result[group]=fallback
    return result


def validate_settings(value):
    if not isinstance(value,dict) or not set(value)<=set(reward_groups()):
        raise ValueError('请选择有效分队')
    def amount(n):
        if type(n) is not int or not 0<=n<=100000:raise ValueError('奖励需为 0–100000 的整数')
    for config in value.values():
        if not isinstance(config,dict) or set(config)!={'daily_checkin_reward','streak_rewards','contest_rewards','practice_rewards'}:
            raise ValueError('奖励配置格式不正确')
        amount(config['daily_checkin_reward'])
        streaks=config['streak_rewards']
        if not isinstance(streaks,list) or len(streaks)>7:raise ValueError('连签规则最多 7 条')
        seen=set()
        for rule in streaks:
            if not isinstance(rule,dict) or type(rule.get('days')) is not int or not 1<=rule['days']<=7 or rule['days'] in seen:
                raise ValueError('连签天数需为 1–7 且不能重复')
            amount(rule.get('reward'));seen.add(rule['days'])
        for field in ('contest_rewards','practice_rewards'):
            rows=config[field]
            if not isinstance(rows,dict) or set(rows)!=set(PLATFORMS):raise ValueError('请配置各平台奖励')
            for platform,values in rows.items():
                if not isinstance(values,list) or len(values)!=3:raise ValueError('题目奖励需填写三档')
                for number in values:amount(number)
                if platform=='qoj' and len(set(values))!=1:raise ValueError('QOJ 使用统一单题奖励')


def amount_for(team,platform,group,difficulty=None,source='contest',day=None,level=None):
    from .contest_rewards import THRESHOLDS,difficulty_value
    config=(team.reward_settings or {}).get(group,{})
    rows=config.get(source+'_rewards',{})
    if level is None:level=grade_for_year(int(group),day)
    legacy_group='beginner' if level==0 else 'experienced'
    fallback=([2,2,2] if platform=='qoj' else [0,0,0] if source=='practice' else (team.problem_rewards or default_problem_rewards()).get(legacy_group,[1,2,3]))
    values=rows.get(platform,fallback)
    if not isinstance(values,list) or len(values)!=3 or any(type(n) is not int or not 0<=n<=100000 for n in values):
        return 0
    if platform=='qoj':return values[0]
    low,high=THRESHOLDS[platform][legacy_group]
    value=difficulty_value(difficulty)
    index=0 if value is None or value<low else 1 if value<high else 2
    return values[index]


def daily_amount(member,day):
    group=group_for(member.user,day)
    return (member.team.reward_settings or {}).get(group,{}).get('daily_checkin_reward',member.team.daily_checkin_reward)


def streak_rules(member,day):
    group=group_for(member.user,day)
    config=(member.team.reward_settings or {}).get(group,{})
    if 'streak_rewards' not in config:return list(member.team.streak_rewards.all())
    return [SimpleNamespace(**rule) for rule in sorted(config['streak_rewards'],key=lambda r:r['days'])]
