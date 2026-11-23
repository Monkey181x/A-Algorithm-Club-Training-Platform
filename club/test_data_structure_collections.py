from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import DataStructureCollection, Submission, Team, TeamMembership


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class DataStructureCollectionTests(TestCase):
    endpoint = "/api/data-structures"
    config = {
        "name": "并查集",
        "requirements": "支持路径压缩",
        "languages": ["cpp", "python"],
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
        self.assertEqual(self.client.get(url).json()["submitted_languages"], ["python"])
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
        self.assertEqual(self.submit(url).status_code, 409)

        self.log_in(self.reader)
        published = self.client.get(entry_url).json()
        self.assertEqual(published["code"], resubmit["code"])
        self.assertEqual(published["blog"], resubmit["blog"])

    def test_submission_is_team_scoped_and_one_per_language(self):
        url = self.create_collection()
        self.log_in(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.submit(url).status_code, 404)

        self.log_in(self.member)
        self.assertEqual(self.submit(url, "java").status_code, 400)
        saved = self.submit(url)
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(self.submit(url).status_code, 409)
        self.assertEqual(self.submit(url, "cpp").status_code, 201)
        entry_url = f"{url}/implementations/{saved.json()['id']}"
        self.assertEqual(self.client.get(entry_url).json()["code"], self.code)
        updated = self.client.patch(entry_url, {"code": "# updated"}, content_type="application/json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(self.client.get(entry_url).json()["code"], "# updated")
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
