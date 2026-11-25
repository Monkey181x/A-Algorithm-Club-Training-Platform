from datetime import datetime, time, timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from openpyxl import load_workbook
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from .astrbot_notifications import queue_weekly_reminders
from .models import (
    AstrBotNotification, CheckinRecord, DataStructureCollection, DataStructureImplementation, Semester,
    SemesterMemberTotal, Submission, SubmissionPart, Team, TeamMembership, TeamNotificationSettings, UserProfile,
)
from .semester_calendar import align_semester
from .services import build_weeks, progress_rows, settle_week


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class DataStructureCollectionTests(TestCase):
    endpoint = "/api/data-structures"
    config = {
        "name": "并查集",
        "requirements": "支持路径压缩",
        "languages": ["cpp", "python", "text"],
        "is_open": True,
    }
    code = "def find(x):\n    return parent[x]\n"

    @classmethod
    def setUpTestData(cls):
        creator = User.objects.create_user('structure-team-creator')
        team = Team.objects.create(name='一队', code='structures-one', created_by=creator)
        other_team = Team.objects.create(name='二队', code='structures-two', created_by=creator)
        cls.admin = cls.create_member(team, "structure-admin", TeamMembership.ADMIN)
        cls.member = cls.create_member(team, "structure-member")
        cls.reader = cls.create_member(team, "structure-reader")
        cls.outsider = cls.create_member(other_team, "structure-outsider", TeamMembership.ADMIN)

    @staticmethod
    def create_member(team, name, role=TeamMembership.MEMBER):
        user = User.objects.create_user(name)
        return TeamMembership.objects.create(team=team, user=user, display_name=name, role=role)

    def log_in(self, member):
        self.client.force_login(member.user)
        session = self.client.session
        session["team_id"] = member.team_id
        session.save()

    def create_collection(self):
        self.log_in(self.admin)
        result = self.client.post(
            self.endpoint, {**self.config, "team_id": self.admin.team_id}, content_type="application/json",
        )
        self.assertEqual(result.status_code, 201, result.content)
        return f"{self.endpoint}/{result.json()['id']}"

    def submit(self, url, language="python"):
        return self.client.post(
            f"{url}/implementations", {"language": language, "code": self.code},
            content_type="application/json",
        )

    def test_team_switch_blocks_member_actions_and_preserves_project_states_and_history(self):
        open_url = self.create_collection()
        closed = DataStructureCollection.objects.create(
            team=self.member.team, name="关闭的项目", languages=["python"], is_open=False,
        )
        today = timezone.localdate()
        semester = Semester.objects.create(
            team=self.member.team, name="训练", starts_on=today, ends_on=today + timedelta(days=7),
        )
        week = build_weeks(semester)[0]
        self.log_in(self.member)
        saved = self.submit(open_url).json()
        rejected = self.submit(open_url).json()
        saved_url = f"{open_url}/implementations/{saved['id']}"
        rejected_url = f"{open_url}/implementations/{rejected['id']}"
        self.assertEqual(self.client.get("/api/me").json()["team"]["data_structures_enabled"], True)
        self.log_in(self.admin)
        result = self.client.patch(
            rejected_url, {"action": "reject", "rejection_note": "请修改"}, content_type="application/json",
        )
        self.assertEqual(result.status_code, 200)

        settings_url = f"/api/management/teams/{self.member.team_id}/settings"
        root = User.objects.get(username="admin")
        self.client.force_login(root)
        settings = self.client.get(settings_url).json()
        self.assertTrue(settings["data_structures_enabled"])
        payload = {**settings["semester"], "name": settings["name"], "code": settings["code"],
                   "settings_version": settings["settings_version"]}
        invalid = self.client.put(settings_url, {**payload, "data_structures_enabled": 0}, content_type="application/json")
        self.assertEqual(invalid.status_code, 400)
        result = self.client.put(settings_url, {**payload, "data_structures_enabled": False}, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(self.client.get(settings_url).json()["data_structures_enabled"])

        self.log_in(self.member)
        self.assertFalse(self.client.get("/api/me").json()["team"]["data_structures_enabled"])
        for url in (self.endpoint, open_url, saved_url, self.endpoint + "?scope=manage"):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.submit(open_url).status_code, 403)
        for url, edit in ((saved_url, {"code": "new"}), (rejected_url, {"action": "resubmit", "code": "new"})):
            self.assertEqual(self.client.patch(url, edit, content_type="application/json").status_code, 403)
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 2}, {self.member.pk: 2}))
        history = self.client.get(f"/api/members/{self.member.pk}/history", {"week": week.number}).json()
        self.assertEqual([row["id"] for row in history["data_structure_submissions"]], [saved["id"]])

        self.log_in(self.admin)
        managed = self.client.get(self.endpoint + "?scope=manage").json()
        self.assertEqual({row["is_open"] for row in managed["collections"]}, {True, False})
        self.assertFalse(self.client.get(open_url + "?scope=manage").json()["can_submit"])
        self.assertEqual(self.client.get(saved_url + "?scope=manage").json()["code"], self.code)
        self.log_in(self.outsider)
        self.assertTrue(self.client.get("/api/me").json()["team"]["data_structures_enabled"])
        self.assertEqual(self.client.get(self.endpoint).status_code, 200)

        self.client.force_login(root)
        payload["settings_version"] = self.client.get(settings_url).json()["settings_version"]
        result = self.client.put(settings_url, {**payload, "data_structures_enabled": True}, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.log_in(self.member)
        self.assertTrue(self.client.get("/api/me").json()["team"]["data_structures_enabled"])
        self.assertEqual(self.submit(open_url).status_code, 201)
        self.assertEqual(self.submit(f"{self.endpoint}/{closed.pk}").status_code, 409)

    def test_configured_rules_snapshot_counts_and_points_independently(self):
        url = self.create_collection()
        today = timezone.localdate()
        semester = Semester.objects.create(
            team=self.member.team, name="训练", starts_on=today, ends_on=today + timedelta(days=7),
        )
        week = build_weeks(semester)[0]
        self.log_in(self.member)
        original = self.submit(url).json()
        self.assertEqual((original["training_submissions"], original["training_points"]), (2, 2))

        root = User.objects.get(username="admin")
        self.client.force_login(root)
        settings_url = f"/api/management/teams/{self.member.team_id}/settings"
        settings = self.client.get(settings_url).json()
        payload = {**settings["semester"], "name": settings["name"], "code": settings["code"],
                   "settings_version": settings["settings_version"]}
        payload.update(data_structure_valid_submissions=1, data_structure_code_points=0, data_structure_blog_points=0)
        response = self.client.put(settings_url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        updated = self.client.get(settings_url).json()
        self.assertEqual(updated["semester"]["data_structure_code_points"], 0)
        payload["settings_version"] = updated["settings_version"]
        invalid = self.client.put(settings_url, {**payload, "data_structure_blog_points": True}, content_type="application/json")
        self.assertEqual(invalid.status_code, 400)

        self.log_in(self.member)
        rules = self.client.get(url).json()["training_rules"]
        self.assertEqual(rules, {"submissions": 1, "code_points": 0, "blog_points": 0, "available": True})
        first_url = f"{url}/implementations/{original['id']}"
        saved_rules = self.client.get(first_url).json()["training_rules"]
        self.assertEqual(saved_rules, {"submissions": 2, "code_points": 2, "blog_points": 2})
        edited = self.client.patch(first_url, {"code": self.code, "blog": "补写说明"}, content_type="application/json").json()
        self.assertEqual((edited["training_submissions"], edited["training_points"]), (2, 4))
        count_only = self.submit(url).json()
        self.assertEqual((count_only["training_submissions"], count_only["training_points"]), (1, 0))
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 4}, {self.member.pk: 3}))
        history = self.client.get(f"/api/members/{self.member.pk}/history", {"week": week.number}).json()
        self.assertIn((1, 0), {(r["training_submissions"], r["training_points"]) for r in history["data_structure_submissions"]})

        self.log_in(self.admin)
        second_url = f"{url}/implementations/{count_only['id']}"
        rejected = self.client.patch(
            second_url, {"action": "reject", "rejection_note": "请修改"}, content_type="application/json",
        ).json()
        self.assertEqual((rejected["training_submissions"], rejected["training_points"]), (0, 0))
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 4}, {self.member.pk: 2}))

        self.client.force_login(root)
        payload.update(data_structure_valid_submissions=0, data_structure_code_points=3, data_structure_blog_points=2)
        response = self.client.put(settings_url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.log_in(self.member)
        resubmitted = self.client.patch(
            second_url, {"action": "resubmit", "code": self.code, "blog": "补写说明"}, content_type="application/json",
        ).json()
        self.assertEqual((resubmitted["training_submissions"], resubmitted["training_points"]), (1, 0))
        points_only = self.client.post(
            f"{url}/implementations", {"language": "python", "code": self.code, "blog": "说明"}, content_type="application/json",
        ).json()
        self.assertEqual((points_only["training_submissions"], points_only["training_points"]), (0, 5))
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 9}, {self.member.pk: 3}))
        totals = SemesterMemberTotal.objects.get(semester=semester, member=self.member)
        self.assertEqual((totals.total_submissions, totals.total_score), (3, 9))

        self.log_in(self.admin)
        export = self.client.get("/api/export", {"semester": semester.pk})
        book = load_workbook(BytesIO(b"".join(export.streaming_content)))
        credits = {row[6:] for row in list(book["数据结构提交"].values)[1:]}
        self.assertEqual(credits, {(2, 4), (1, 0), (0, 5)})

    def test_zero_credit_history_stays_visible_to_the_author_when_the_team_disables_structures(self):
        url = self.create_collection()
        today = timezone.localdate()
        semester = Semester.objects.create(
            team=self.member.team, name="训练", starts_on=today, ends_on=today + timedelta(days=7),
            data_structure_valid_submissions=0, data_structure_code_points=0, data_structure_blog_points=0,
        )
        week = build_weeks(semester)[0]
        self.log_in(self.member)
        response = self.submit(url)
        self.assertEqual(response.status_code, 201, response.content)
        entry = response.json()
        self.assertEqual((entry["training_submissions"], entry["training_points"]), (0, 0))
        Team.objects.filter(pk=self.member.team_id).update(data_structures_enabled=False)
        member_url = f"/api/members/{self.member.pk}"
        history = self.client.get(f"{member_url}/history", {"week": week.number}).json()["data_structure_submissions"]
        profile = self.client.get(f"{member_url}/profile").json()["submissions"]
        self.assertEqual(profile, history)
        self.assertEqual([record["id"] for record in history], [entry["id"]])
        self.assertTrue(history[0]["can_view"])
        detail = f"{url}/implementations/{entry['id']}"
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(self.client.get(detail, {"scope": "history"}).json()["code"], self.code)
        self.assertEqual(self.client.patch(detail + "?scope=history", {"code": "change"}, content_type="application/json").status_code, 403)
        self.assertEqual(progress_rows(semester, week), ({}, {}))
        for viewer in (self.reader, self.outsider):
            self.log_in(viewer)
            self.assertEqual(self.client.get(detail, {"scope": "history"}).status_code, 404)
            if viewer == self.reader:
                self.assertFalse(self.client.get(f"{member_url}/profile").json()["submissions"][0]["can_view"])

    def test_structure_credit_date_survives_edits_and_guards_both_term_boundaries(self):
        url = self.create_collection()
        self.log_in(self.member)
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        term = Semester.objects.create(team=self.member.team, name="训练", starts_on=monday, ends_on=sunday)
        week = build_weeks(term)[0]
        with patch("django.utils.timezone.now", return_value=week.starts_at + timedelta(hours=12)):
            first = self.submit(url).json()
        with patch("django.utils.timezone.now", return_value=week.ends_at - timedelta(hours=12)):
            self.assertEqual(self.submit(url).status_code, 201)
            edit = self.client.patch(f"{url}/implementations/{first['id']}", {"code": "updated"}, content_type="application/json")
            self.assertEqual(edit.status_code, 200)
        self.assertEqual(DataStructureImplementation.objects.get(pk=first["id"]).training_date, monday)
        for dates in [(monday + timedelta(days=1), sunday), (monday, sunday - timedelta(days=1))]:
            with self.assertRaisesMessage(ValidationError, "范围外的数据结构提交"):
                align_semester(term, dates=dates)
        DataStructureImplementation.objects.update(training_date=None)
        with self.assertRaisesMessage(ValidationError, "日期未知"):
            align_semester(term, dates=(monday + timedelta(days=1), sunday))
        align_semester(term, dates=(monday - timedelta(days=1), sunday + timedelta(days=1)))

    def test_active_export_includes_structure_credit_and_rejections(self):
        url = self.create_collection()
        today = timezone.localdate()
        term = Semester.objects.create(team=self.member.team, name="训练", starts_on=today, ends_on=today + timedelta(days=7))
        DataStructureCollection.objects.filter(team=self.member.team).update(name="=literal")
        self.log_in(self.member)
        first = self.submit(url).json()
        second = self.client.post(f"{url}/implementations", {"language":"text", "code":"ok", "blog":"说明"},
                                  content_type="application/json").json()
        self.log_in(self.admin)
        rejection = self.client.patch(f"{url}/implementations/{first['id']}", {"action":"reject", "rejection_note":"请修改"},
                                      content_type="application/json")
        self.assertEqual(rejection.status_code, 200)
        response = self.client.get("/api/export", {"semester": term.pk})
        book = load_workbook(BytesIO(b"".join(response.streaming_content)))
        self.assertEqual(book.sheetnames, ["提交与审核", "数据结构提交"])
        sheet = book["数据结构提交"]
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertEqual(sheet["E3"].value.date(), today)
        rows = list(sheet.values)[1:]
        self.assertEqual([r[5:] for r in rows], [("已驳回", 0, 0), ("已提交", 2, 4)])
        self.assertEqual(rows[1][:4], (self.member.display_name, "=literal", "纯文本", 1))
        self.assertEqual(second["training_points"], 4)

    def test_retried_create_counts_once_and_history_explains_credit(self):
        url = self.create_collection()
        self.log_in(self.member)
        today = timezone.localdate()
        semester = Semester.objects.create(team=self.member.team, name="训练", starts_on=today, ends_on=today + timedelta(days=13))
        week = build_weeks(semester)[0]
        payload = {"language": "text", "code": "one", "request_id": str(uuid4())}
        def post():
            return self.client.post(f"{url}/implementations", payload, content_type="application/json")

        first = post()
        self.assertEqual(first.status_code, 201)
        replay = post()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()["id"], replay.json()["id"])
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 2}, {self.member.pk: 2}))
        payload["code"] = "two"
        self.assertEqual(post().status_code, 409)
        payload["request_id"] = str(uuid4())
        payload["blog"] = " blog "
        second = post()
        self.assertEqual(second.status_code, 201)
        self.assertEqual(post().status_code, 200)
        self.assertEqual(progress_rows(semester, week), ({self.member.pk: 6}, {self.member.pk: 4}))
        with CaptureQueriesContext(connection) as queries:
            listing = self.client.get(url)
        self.assertEqual(len(listing.json()["implementations"]), 2)
        selects = [q["sql"] for q in queries if 'FROM "club_datastructureimplementation"' in q["sql"]]
        self.assertTrue(selects)
        for sql in selects:
            for field in ("code", "blog", "notes"):
                self.assertNotIn(f'"club_datastructureimplementation"."{field}"', sql)
        self.log_in(self.reader)
        params = {"week": week.number, "semester": semester.pk}
        history = self.client.get(f"/api/members/{self.member.pk}/history", params).json()
        self.assertEqual([r["training_points"] for r in history["data_structure_submissions"]], [4, 2])
        self.assertEqual(history["summary"]["score"], 6)
        profile = self.client.get(f"/api/members/{self.member.pk}/profile").json()
        self.assertEqual(profile["total"], 2)
        self.assertEqual(profile["submissions"], history["data_structure_submissions"])

    def test_create_rejects_a_stale_team_selection(self):
        self.log_in(self.admin)
        TeamMembership.objects.create(
            team=self.outsider.team, user=self.admin.user, display_name="跨团队管理员", role=TeamMembership.ADMIN,
        )
        listed = self.client.get(f"{self.endpoint}?scope=manage").json()
        self.assertEqual(listed["team_id"], self.admin.team_id)
        switched = self.client.post(
            "/api/team/select", {"team_id": self.outsider.team_id}, content_type="application/json",
        )
        self.assertEqual(switched.status_code, 200)
        stale = self.client.post(
            self.endpoint, {**self.config, "team_id": listed["team_id"]}, content_type="application/json",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(DataStructureCollection.objects.exists())
        current = self.client.post(
            self.endpoint, {**self.config, "team_id": self.outsider.team_id}, content_type="application/json",
        )
        self.assertEqual(current.status_code, 201, current.content)
        self.assertEqual(DataStructureCollection.objects.get().team_id, self.outsider.team_id)

    def test_edit_rejects_stale_settings_and_accepts_current_version(self):
        url = self.create_collection()
        second_admin = self.create_member(self.admin.team, "structure-second-admin", TeamMembership.ADMIN)
        self.log_in(second_admin)
        snapshot = self.client.get(url).json()["collection"]

        self.log_in(self.admin)
        updated = self.client.patch(
            url, {**snapshot, "is_open": False, "languages": ["python"]}, content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["version"], snapshot["version"] + 1)

        self.log_in(second_admin)
        stale = self.client.patch(url, {**snapshot, "name": "新名称"}, content_type="application/json")
        self.assertEqual(stale.status_code, 409)
        current = self.client.get(url).json()["collection"]
        self.assertEqual(current, updated.json())
        saved = self.client.patch(url, {**current, "name": "新名称"}, content_type="application/json")
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertFalse(saved.json()["is_open"])
        self.assertEqual(saved.json()["languages"], ["python"])
        self.assertEqual(saved.json()["name"], "新名称")

    def test_empty_collection_can_be_deleted(self):
        url = self.create_collection()
        self.log_in(self.member)
        self.assertEqual(self.client.delete(url).status_code, 403)

        self.log_in(self.admin)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(self.endpoint).json()["collections"], [])

        self.log_in(self.member)
        self.assertEqual(self.submit(url).status_code, 404)
        self.assertIsNotNone(DataStructureCollection.objects.get().deleted_at)

    def test_rejection_and_resubmission_respect_visibility_and_ownership(self):
        url = self.create_collection()
        self.log_in(self.member)
        saved = self.submit(url)
        self.assertEqual(saved.status_code, 201, saved.content)
        entry_url = f"{url}/implementations/{saved.json()['id']}"
        reject = {"action": "reject", "rejection_note": "请补充合并操作"}
        self.assertEqual(self.client.patch(entry_url, reject, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(f"{url}?scope=manage").status_code, 403)
        self.assertEqual(self.client.get(f"{entry_url}?scope=manage").status_code, 403)

        self.log_in(self.admin)
        self.assertEqual(self.client.delete(url).status_code, 409)
        empty = self.client.patch(entry_url, {**reject, "rejection_note": ""}, content_type="application/json")
        self.assertEqual(empty.status_code, 400)
        stale = self.client.patch(entry_url, {**reject, "version": "old"}, content_type="application/json")
        self.assertEqual(stale.status_code, 409)
        result = self.client.patch(entry_url, reject, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.json()["is_rejected"])
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertEqual(self.client.get(self.endpoint).json()["collections"][0]["implementation_count"], 0)
        managed = self.client.get(f"{self.endpoint}?scope=manage").json()["collections"]
        self.assertEqual(managed[0]["implementation_count"], 1)
        self.assertEqual(self.client.get(f"{entry_url}?scope=manage").status_code, 200)

        self.log_in(self.reader)
        self.assertEqual(self.client.get(f"{self.endpoint}?scope=mine").json()["collections"], [])
        self.assertEqual(self.client.get(url).json()["implementations"], [])
        self.assertEqual(self.client.get(f"{entry_url}?scope=mine").status_code, 404)
        self.assertEqual(self.client.get(f"{entry_url}?scope=browse").status_code, 404)
        self.assertEqual(self.client.get(f"{url}?scope=browse").json()["implementations"], [])
        resubmit = {"action": "resubmit", "code": "# fixed", "blog": "## 更新"}
        self.assertEqual(self.client.patch(entry_url, resubmit, content_type="application/json").status_code, 403)

        self.log_in(self.member)
        self.assertEqual(self.client.get(entry_url).status_code, 404)
        own = self.client.get(f"{self.endpoint}?scope=mine").json()["collections"]
        self.assertEqual(own[0]["implementation_count"], 1)
        original = self.client.get(f"{entry_url}?scope=mine").json()
        self.assertEqual(original["code"], self.code)
        self.assertEqual(original["rejection_note"], "请补充合并操作")
        self.assertEqual(self.client.get(f"{entry_url}?scope=browse").json(), original)
        self.assertEqual(self.client.get(f"{url}?scope=browse").json()["collection"]["implementation_count"], 1)
        self.assertEqual(
            self.client.patch(entry_url, {"code": "# stale edit"}, content_type="application/json").status_code, 409,
        )
        updated = self.client.patch(entry_url, {**resubmit, "version": original["updated_at"]},
                                    content_type="application/json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["id"], original["id"])
        self.assertFalse(updated.json()["is_rejected"])
        self.assertEqual(updated.json()["rejection_note"], "")
        self.assertEqual(DataStructureImplementation.objects.count(), 1)

        self.log_in(self.reader)
        published = self.client.get(entry_url).json()
        self.assertEqual(published["code"], resubmit["code"])
        self.assertEqual(published["blog"], resubmit["blog"])

    def test_submission_is_team_scoped_and_allows_repeated_languages(self):
        url = self.create_collection()
        self.log_in(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.submit(url).status_code, 404)

        self.log_in(self.member)
        self.assertEqual(self.submit(url, "java").status_code, 400)
        saved = self.submit(url)
        self.assertEqual(saved.status_code, 201, saved.content)
        another = self.submit(url)
        self.assertEqual(another.status_code, 201)
        self.assertNotEqual(saved.json()["id"], another.json()["id"])
        self.assertEqual(self.submit(url, "cpp").status_code, 201)
        entry_url = f"{url}/implementations/{saved.json()['id']}"
        self.assertEqual(self.client.get(entry_url).json()["code"], self.code)
        updated = self.client.patch(entry_url, {"code": "# updated"}, content_type="application/json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(self.client.get(entry_url).json()["code"], "# updated")
        self.assertEqual(self.client.get(f"{url}/implementations/{another.json()['id']}").json()["code"], self.code)
        self.assertFalse(Submission.objects.exists())

        self.log_in(self.reader)
        summary = self.client.get(f"{url}?scope=browse").json()["implementations"][0]
        self.assertEqual(summary["member_id"], self.member.pk)
        self.assertEqual(summary["author"], self.member.display_name)
        self.assertIn("updated_at", summary)
        self.assertNotIn("code", summary)
        self.assertNotIn("blog", summary)
        self.assertEqual(self.client.get(entry_url).status_code, 200)
        self.assertEqual(
            self.client.patch(entry_url, {"code": "different"}, content_type="application/json").status_code,
            403,
        )

    def test_collection_status_uses_only_the_viewers_submissions(self):
        url = self.create_collection()
        browse = f"{self.endpoint}?scope=browse"

        def status():
            return self.client.get(browse).json()["collections"][0]["my_submission_status"]

        self.log_in(self.member)
        self.assertEqual(status(), "unsubmitted")
        python = self.submit(url).json()["id"]
        cpp = self.submit(url, "cpp").json()["id"]
        self.assertEqual(status(), "submitted")

        self.log_in(self.reader)
        self.assertEqual(status(), "unsubmitted")

        reject = {"action": "reject", "rejection_note": "请补充测试"}
        for entry_id, expected in [(python, "submitted"), (cpp, "rejected")]:
            self.log_in(self.admin)
            response = self.client.patch(f"{url}/implementations/{entry_id}", reject, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.log_in(self.member)
            self.assertEqual(status(), expected)

        response = self.client.patch(
            f"{url}/implementations/{python}", {"action": "resubmit", "code": self.code},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(status(), "submitted")

    def test_admin_order_is_team_scoped_and_new_collections_append(self):
        first = int(self.create_collection().rsplit("/", 1)[1])
        second = int(self.create_collection().rsplit("/", 1)[1])
        order_url = f"{self.endpoint}/order"

        def displayed_ids(scope="browse"):
            data = self.client.get(f"{self.endpoint}?scope={scope}").json()
            return [item["id"] for item in data["collections"]]

        def reorder(before, after):
            return self.client.patch(
                order_url, {"previous_ids": before, "ids": after}, content_type="application/json",
            )

        self.assertEqual(displayed_ids("manage"), [first, second])
        self.log_in(self.member)
        self.assertEqual(reorder([first, second], [second, first]).status_code, 403)

        self.log_in(self.outsider)
        other = self.client.post(
            self.endpoint, {**self.config, "team_id": self.outsider.team_id}, content_type="application/json",
        ).json()["id"]
        self.assertEqual(reorder([first, second], [second, first]).status_code, 409)
        self.assertEqual(displayed_ids(), [other])

        self.log_in(self.admin)
        self.assertEqual(reorder([first, second], [first, first]).status_code, 400)
        self.assertEqual(reorder([first, second], [second, other]).status_code, 400)
        result = reorder([first, second], [second, first])
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(displayed_ids("manage"), [second, first])
        self.assertEqual(reorder([first, second], [second, first]).status_code, 409)

        third = int(self.create_collection().rsplit("/", 1)[1])
        self.assertEqual(reorder([second, first], [first, second]).status_code, 409)
        self.log_in(self.member)
        self.assertEqual(displayed_ids(), [second, first, third])

    def test_training_credit_tracks_original_week_through_blog_changes_and_rejection(self):
        url = self.create_collection()
        self.log_in(self.member)
        legacy = self.submit(url, "cpp")
        self.assertEqual(legacy.status_code, 201, legacy.content)
        self.assertEqual(legacy.json()["training_points"], 0)

        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        semester = Semester.objects.create(
            team=self.member.team, name="计分测试", starts_on=monday, ends_on=monday + timedelta(days=13),
            min_submissions=2, min_score=4,
        )
        first_week, second_week = build_weeks(semester)
        self.assertTrue(self.client.get(url).json()["training_rules"]["available"])
        fresh = self.submit(url)
        self.assertEqual(fresh.status_code, 201, fresh.content)
        self.assertEqual((fresh.json()["training_submissions"], fresh.json()["training_points"]), (2, 2))
        entry_url = f"{url}/implementations/{fresh.json()['id']}"
        legacy_url = f"{url}/implementations/{legacy.json()['id']}"
        edited_legacy = self.client.patch(
            legacy_url, {"code": self.code, "blog": "旧实现补 Blog"}, content_type="application/json",
        )
        self.assertEqual(edited_legacy.json()["training_points"], 0)

        regular = Submission.objects.create(
            team=self.member.team, member=self.member, semester=semester, week=first_week, record_date=today,
        )
        SubmissionPart.objects.create(
            submission=regular, kind=SubmissionPart.PROOF, status=SubmissionPart.APPROVED, points_awarded=1,
        )
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual((dashboard["submissions"], dashboard["score"]), (3, 3))
        self.assertFalse(CheckinRecord.objects.exists())

        edited = self.client.patch(
            entry_url, {"code": self.code, "blog": "支持按秩合并"}, content_type="application/json",
        )
        self.assertEqual((edited.json()["training_submissions"], edited.json()["training_points"]), (2, 4))
        settled = settle_week(first_week)
        self.assertTrue(settled.is_closed)
        total = SemesterMemberTotal.objects.get(semester=semester, member=self.member)
        self.assertEqual((total.total_submissions, total.total_score, total.qualified_weeks), (3, 5, 1))

        next_week = timezone.make_aware(datetime.combine(monday + timedelta(days=7), time(12)))
        with patch("django.utils.timezone.now", return_value=next_week):
            self.log_in(self.admin)
            rejected = self.client.patch(
                entry_url, {"action": "reject", "rejection_note": "请修正复杂度"}, content_type="application/json",
            )
            self.assertEqual((rejected.json()["training_submissions"], rejected.json()["training_points"]), (0, 0))
            total.refresh_from_db()
            self.assertEqual((total.total_submissions, total.total_score, total.qualified_weeks), (1, 1, 0))

            self.log_in(self.member)
            retried = self.client.patch(
                entry_url, {"action": "resubmit", "code": self.code, "blog": "   "}, content_type="application/json",
            )
            self.assertEqual((retried.json()["training_submissions"], retried.json()["training_points"]), (2, 2))
            self.assertEqual(retried.json()["id"], fresh.json()["id"])

        implementation = DataStructureImplementation.objects.get(pk=fresh.json()["id"])
        self.assertEqual(implementation.training_week_id, first_week.pk)
        scores, counts = progress_rows(semester, second_week, [self.member.pk])
        self.assertEqual((scores.get(self.member.pk, 0), counts.get(self.member.pk, 0)), (0, 0))
        total.refresh_from_db()
        self.assertEqual((total.total_submissions, total.total_score, total.qualified_weeks), (3, 3, 0))

    @override_settings(ASTRBOT_NOTIFY_URL="https://notify.invalid/club/notify")
    def test_weekly_reminder_counts_data_structure_credit_without_new_wording(self):
        url = self.create_collection()
        now = timezone.now()
        today = timezone.localdate(now)
        Semester.objects.create(
            team=self.member.team, name="提醒测试", starts_on=today, ends_on=today + timedelta(days=7),
        )
        TeamNotificationSettings.objects.create(
            team=self.member.team, enabled=True, qualification_enabled=True,
            encrypted_api_token="test", review_weekday=today.weekday(), review_time=time.min,
        )
        for member in (self.member, self.reader):
            UserProfile.objects.create(user=member.user, admission_year=today.year)
        self.log_in(self.member)
        self.assertEqual(self.submit(url, "text").status_code, 201)
        with_blog = self.client.post(
            f"{url}/implementations", {"language": "text", "code": self.code, "blog": "说明"},
            content_type="application/json",
        )
        self.assertEqual(with_blog.json()["training_points"], 4)
        self.assertEqual(queue_weekly_reminders(now), 1)
        reminder = AstrBotNotification.objects.get().payload
        self.assertEqual([item["member_id"] for item in reminder["data"]["members"]], [self.reader.pk])
        self.assertEqual(reminder["text"], f"本周尚未达标的同学有：\n{self.reader.display_name}（积分 0，有效提交 0）")

    def test_archived_training_credit_is_preserved_when_library_code_is_edited(self):
        url = self.create_collection()
        self.log_in(self.member)
        today = timezone.localdate()
        semester = Semester.objects.create(
            team=self.member.team, name="归档测试", starts_on=today, ends_on=today + timedelta(days=6),
        )
        saved = self.submit(url)
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()["training_points"], 2)
        Semester.objects.filter(pk=semester.pk).update(archived_at=timezone.now(), is_active=False)
        entry_url = f"{url}/implementations/{saved.json()['id']}"
        updated = self.client.patch(
            entry_url, {"code": self.code, "blog": "归档后仍可维护代码"}, content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["training_points"], 2)
        details = self.client.get(entry_url).json()
        self.assertTrue(details["training_archived"])
        self.assertEqual(details["blog"], "归档后仍可维护代码")
        total = SemesterMemberTotal.objects.get(semester=semester, member=self.member)
        self.assertEqual((total.total_submissions, total.total_score), (2, 2))

    def test_closing_preserves_published_code(self):
        url = self.create_collection()
        self.log_in(self.member)
        self.assertEqual(self.submit(url).status_code, 201)

        self.log_in(self.admin)
        version = self.client.get(url).json()["collection"]["version"]
        result = self.client.patch(
            url, {**self.config, "is_open": False, "version": version}, content_type="application/json",
        )
        self.assertEqual(result.status_code, 200, result.content)

        self.log_in(self.member)
        detail = self.client.get(url).json()
        self.assertFalse(detail["can_submit"])
        self.assertEqual(len(detail["implementations"]), 1)
        self.assertEqual(self.submit(url, "cpp").status_code, 409)

    def test_blog_is_optional_and_preserves_legacy_notes(self):
        url = self.create_collection()
        self.log_in(self.member)
        notes = "复杂度 O(n)\n*保留原文*"
        saved = self.client.post(
            f"{url}/implementations", {"language": "python", "code": self.code, "notes": notes},
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 201, saved.content)
        entry_url = f"{url}/implementations/{saved.json()['id']}"
        legacy = self.client.get(entry_url).json()
        self.assertEqual(legacy["notes"], notes)
        self.assertIsNone(legacy["blog"])

        blog = "## 实现思路\n时间复杂度 **O(n)**"
        for payload, status in [
            ({"blog": 1}, 400), ({"blog": "x" * 100001}, 400),
            ({"blog": blog}, 200), ({}, 200),
        ]:
            response = self.client.patch(entry_url, {"code": self.code, **payload}, content_type="application/json")
            self.assertEqual(response.status_code, status, response.content)
        published = self.client.get(entry_url).json()
        self.assertEqual(published["blog"], blog)
        self.assertEqual(published["notes"], notes)

        response = self.client.patch(entry_url, {"code": self.code, "blog": ""}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        cleared = self.client.get(entry_url).json()
        self.assertEqual(cleared["blog"], "")
        self.assertEqual(cleared["notes"], notes)

    def test_daily_and_collection_language_options_have_the_same_order(self):
        url = self.create_collection()
        DataStructureCollection.objects.update(languages=["bash", "cpp", "text"])
        self.log_in(self.member)
        collection = self.client.get(url).json()
        self.assertEqual(collection["collection"]["languages"], ["text", "cpp", "bash"])

        html = self.client.get("/").content.decode()
        select = html.split('<select id="submissionCodeLanguage"', 1)[1].split("</select>", 1)[0]
        keys = ("text", "cpp", "c", "python", "java", "javascript", "typescript",
                "go", "rust", "csharp", "kotlin", "swift", "sql", "bash")
        catalog = collection["languages"]
        self.assertEqual(tuple(catalog), keys)
        self.assertEqual(select.split(">", 1)[1], "".join(
            f'<option value="{key}">{catalog[key]}</option>' for key in keys
        ))
