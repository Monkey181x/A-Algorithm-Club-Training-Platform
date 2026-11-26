from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from .cf_tracker import roster_queue, verified_rosters
from .contest_rewards import claim_problems
from .models import ContestProblemClaim, PlatformBinding, PlatformSolvedRecord, TrackerCatalog
from .platform_rewards import claim_rewards
from .platform_sync import SyncError
from .tasks import refresh_tracker_catalog
from .tracker_sources import contest, problem


def round_event(cid, division, labels):
    url = f'https://codeforces.com/contest/{cid}'
    event = contest(cid, f'Codeforces Round 1136 ({division})', url, 1795271700, division)
    event['problems'] = [problem(f'{cid}:{label}', label, name, f'{url}/problem/{label}', 1600)
                         for label, name in labels]
    return event


easy, hard = 'Boundary Stones (Easy Version)', 'Boundary Stones (Hard Version)'
div1 = [('A1', easy), ('A2', hard), ('B', 'Metro'), ('C', 'Math')]
div2 = [('A', 'Duel'), ('B1', easy), ('B2', hard), ('C', 'Youkai'), ('D', 'Metro'), ('E', 'Math')]


def standings(remote, method, params):
    cid = int(params.removeprefix('contestId='))
    assert method == 'contest.standings' and cid in (2287, 2288)
    rows = div1 if cid == 2287 else div2
    return {'contest': {'id': cid}, 'problems': [{'index': label, 'name': name} for label, name in rows]}


class CodeforcesTrackerTests(TestCase):
    def setUp(self):
        self.events = [round_event(2287, 'Div. 1', div1), round_event(2288, 'Div. 2', [div2[0], div2[3]])]

    def test_refresh_backfills_then_preserves_roster_and_skips_verified_round(self):
        token = uuid4()
        catalog = TrackerCatalog.objects.create(kind='codeforces', token=token, status='pending')
        with patch('club.tracker_sources.fetch', side_effect=lambda *args: (self.events, {}, True)), \
             patch('club.cf_tracker.cf', side_effect=standings) as request:
            refresh_tracker_catalog('codeforces', str(token))
            catalog.refresh_from_db()
            self.assertEqual(catalog.refresh_state['groups'], [['2287', '2288']])
            refresh_tracker_catalog('codeforces', str(token))
            catalog.refresh_from_db()
            rows = catalog.contests[1]['problems']
            self.assertEqual([p['label'] for p in rows], [label for label, _ in div2])
            self.assertTrue(all(p['url'].endswith('/2288/problem/' + p['label']) for p in rows))
            self.assertEqual(rows[1]['canonical_key'], '2287:a1')
            self.assertEqual(rows[2]['canonical_key'], '2287:a2')
            self.events[0]['problems'][0]['difficulty'] = 1900
            catalog.status = 'pending'
            catalog.save()
            refresh_tracker_catalog('codeforces', str(token))
            catalog.refresh_from_db()
            self.assertEqual(catalog.status, 'ready')
            self.assertEqual(catalog.contests[1]['problems'], [dict(p, difficulty=1900) if p['label'] == 'B1'
                                                               else p for p in rows])
            self.assertEqual(request.call_count, 2)

    def test_failed_pair_keeps_both_previous_rosters_and_retries(self):
        token = uuid4()
        catalog = TrackerCatalog.objects.create(kind='codeforces', token=token, status='pending',
            contests=self.events, refresh_state={'phase': 'cf_rosters', 'groups': [['2287', '2288']]})
        with self.assertLogs('club.tasks', level='WARNING'), patch('club.cf_tracker.cf', side_effect=[
                standings(None, 'contest.standings', 'contestId=2287'), SyncError('rate limited')]):
            refresh_tracker_catalog('codeforces', str(token))
        catalog.refresh_from_db()
        self.assertEqual(catalog.status, 'failed')
        self.assertEqual([e['problems'] for e in catalog.contests], [e['problems'] for e in self.events])
        retry = catalog.contests[0]['roster_retry_at']
        self.assertEqual(roster_queue(catalog.contests, retry - 1), [])
        self.assertEqual(roster_queue(catalog.contests, retry), [['2287', '2288']])

    def test_unrelated_rounds_stay_separate(self):
        self.events[0]['title'] = 'Codeforces Round 999 (Div. 1)'
        self.assertCountEqual(roster_queue(self.events, 0), [['2287'], ['2288']])

    def test_recent_partial_standings_are_rechecked_without_new_problemset_keys(self):
        now = timezone.now()
        for event in self.events:
            event['starts_at'] = now.timestamp() - 7200
        def partial(remote, method, params):
            data = standings(remote, method, params)
            if params == 'contestId=2288':
                data['problems'] = [p for p in data['problems'] if p['index'] in ('A', 'C')]
            return data
        with patch('django.utils.timezone.now', return_value=now), patch('club.cf_tracker.cf', side_effect=partial):
            first = verified_rosters(None, self.events)
        self.assertEqual(len(first[1]['problems']), 2)
        self.assertEqual(roster_queue(first, now.timestamp()), [])
        later = now + timedelta(days=1)
        self.assertEqual(roster_queue(first, later.timestamp()), [['2287', '2288']])
        with patch('django.utils.timezone.now', return_value=later), patch('club.cf_tracker.cf', side_effect=standings):
            second = verified_rosters(None, first)
        self.assertEqual(len(second[1]['problems']), 6)
        self.assertEqual(roster_queue(second, later.timestamp()), [])

    def test_targeted_recheck_resolves_only_selected_pair_and_preserves_other_retries(self):
        from .test_contest_progress import ProgressTests
        ProgressTests.setUp(self)
        retry = timezone.now().timestamp() + 86400
        other = round_event(77, 'Div. 3', [])
        for event in [*self.events, other]:
            event['roster_retry_at'] = retry
        catalog = TrackerCatalog.objects.create(kind='codeforces', contests=[*self.events, other])
        response = self.client.post('/api/trackers/codeforces/refresh?contest=2288')
        self.assertEqual(response.status_code, 202)
        catalog.refresh_from_db()
        self.assertEqual(catalog.refresh_state['groups'], [['2287', '2288']])
        self.assertEqual(self.client.post('/api/trackers/codeforces/refresh?contest=77').status_code, 409)
        with patch('club.cf_tracker.cf', side_effect=standings):
            refresh_tracker_catalog('codeforces', str(catalog.token))
        rows = self.client.get('/api/trackers/codeforces').json()
        self.assertEqual((rows['status'], rows['retry_count'], rows['next_retry_at']), ('failed', 1, retry))
        self.assertEqual(len(next(c for c in rows['contests'] if c['id'] == '2288')['problems']), 6)

    def test_ambiguous_titles_do_not_share_identity(self):
        def ambiguous(remote, method, params):
            result = standings(remote, method, params)
            if result['contest']['id'] == 2288:
                result['problems'].append({'index': 'F', 'name': easy})
            return result
        with patch('club.cf_tracker.cf', side_effect=ambiguous):
            _, second = verified_rosters(Mock(), self.events)
        matches = [p for p in second['problems'] if p['title'] == easy]
        self.assertEqual(len(matches), 2)
        self.assertTrue(all('canonical_key' not in p for p in matches))
        self.assertTrue(all(p['identity_pending'] for p in matches))

    def test_shared_ac_and_single_reward_with_local_contest_evidence(self):
        from .test_contest_progress import ProgressTests
        ProgressTests.setUp(self)
        with patch('club.cf_tracker.cf', side_effect=standings):
            events = verified_rosters(Mock(), self.events)
        other = round_event(77, 'Div. 3', [('A', 'Independent')])
        catalog = TrackerCatalog.objects.create(kind='codeforces', contests=[*self.events, other],
                                                updated_at=timezone.now() - timedelta(minutes=1))
        binding = PlatformBinding.objects.create(user=self.user, platform='codeforces', account='reader')
        self.team.reward_settings = {'beginner': {'practice_rewards': {'codeforces': [2, 2, 2]}}}
        self.team.save()
        PlatformSolvedRecord.objects.create(binding=binding, problem_key='2288:b1')
        PlatformSolvedRecord.objects.create(binding=binding, problem_key='2287:a1')
        PlatformSolvedRecord.objects.create(binding=binding, problem_key='77:a')
        self.contest.platform, self.contest.account, self.contest.contest_key = 'codeforces', 'reader', '2288'
        self.contest.save()
        self.problem.problem_key, self.problem.label, self.problem.url = 'B1', 'B1', events[1]['problems'][1]['url']
        self.problem.save()
        rows = self.client.get('/api/trackers/codeforces').json()
        self.assertEqual(rows['reward_keys'], ['codeforces|77:a'])
        self.assertTrue(next(c for c in rows['contests'] if c['id'] == '2288')['reward_pending'])
        self.assertEqual(claim_rewards(self.member, keys=['codeforces|2288:b1'])['total'], 0)
        self.assertEqual(claim_problems(self.member)['total'], 0)
        self.assertEqual(claim_rewards(self.member)['total'], 2)
        catalog.contests = events
        catalog.updated_at = timezone.now()
        catalog.save()
        rows = self.client.get('/api/trackers/codeforces').json()
        solved = [(c['id'], p['label']) for c in rows['contests'] for p in c['problems'] if p['accepted']]
        self.assertCountEqual(solved, [('2288', 'B1'), ('2287', 'A1')])
        self.assertEqual(rows['reward_keys'], ['codeforces|2287:a1'])
        self.assertEqual(claim_rewards(self.member, platform='codeforces')['total'], 2)
        self.assertEqual(claim_rewards(self.member, platform='codeforces')['total'], 0)
        self.assertEqual(claim_problems(self.member, [self.problem.pk])['total'], 0)
        self.assertFalse(self.user.contest_participations.filter(contest_key='2287').exists())
        # Old installations can also have a contest claim without a platform ledger row.
        ContestProblemClaim.objects.create(user=self.user, member=self.member, team=self.team, platform='codeforces',
            account='reader', contest_key='2287', problem_key='A2', amount=1, group='beginner')
        self.problem.problem_key, self.problem.label, self.problem.url = 'B2', 'B2', events[1]['problems'][2]['url']
        self.problem.save()
        self.assertEqual(claim_problems(self.member, [self.problem.pk])['total'], 0)
