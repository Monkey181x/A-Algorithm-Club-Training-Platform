from uuid import UUID

from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.utils import timezone

from .models import DataStructureCollection, DataStructureImplementation, Semester, Team
from .services import refresh_totals
from .submission_content import (
    CODE_LANGUAGES, DATA_STRUCTURE_BLOG_POINTS, DATA_STRUCTURE_CODE_POINTS,
    DATA_STRUCTURE_VALID_SUBMISSIONS,
)
from .views import active_semester, api_error, api_login_required, body, membership_for, structure_history_rows


def collection_data(item, count):
    return {
        "id": item.pk, "name": item.name, "requirements": item.requirements,
        "languages": [language for language in CODE_LANGUAGES if language in item.languages],
        "is_open": item.is_open, "implementation_count": count, "version": item.version,
    }


def implementation_data(item, membership, include_code=False):
    data = {
        "id": item.pk, "member_id": item.member_id,
        "author": item.member.display_name if item.member else "已注销成员",
        "language": item.language, "is_rejected": item.is_rejected, "updated_at": item.updated_at.isoformat(),
        "rejection_note": item.rejection_note,
        "is_mine": item.member_id == membership.pk,
        "training_points": item.training_points if item.training_week_id else 0,
        "training_submissions": item.training_submissions if item.training_week_id else 0,
    }
    if include_code:
        data.update(
            code=item.code, notes=item.notes, blog=item.blog,
            training_tracked=bool(item.training_week_id),
            training_archived=bool(item.training_week_id and item.training_week.semester.archived_at),
        )
        if item.training_week_id:
            data["training_rules"] = {
                "submissions": item.training_rule_submissions,
                "code_points": item.training_rule_code_points,
                "blog_points": item.training_rule_blog_points,
            }
    return data


def current_training_week(semester, now):
    if semester:
        return semester.weeks.filter(is_closed=False, starts_at__lte=now, ends_at__gt=now).first()
    return None


def update_training_credit(item, semester):
    # The semester lock keeps credit changes and weekly settlement consistent.
    if not semester or semester.archived_at or not item.training_week_id:
        return
    submissions = points = 0
    if not item.is_rejected:
        submissions = item.training_rule_submissions
        points = item.training_rule_code_points
        if (item.blog or "").strip():
            points += item.training_rule_blog_points
    if (item.training_submissions, item.training_points) == (submissions, points):
        return
    item.training_submissions = submissions
    item.training_points = points
    item.save(update_fields=["training_submissions", "training_points"])
    refresh_totals(semester)


def structure_read_error(membership, scope):
    if scope == "manage":
        if not membership.is_admin:
            return api_error("仅管理员可以查看全部提交", 403)
    elif not membership.team.data_structures_enabled:
        return api_error("当前团队已关闭数据结构", 403)


def collection_fields(data):
    name = data.get("name")
    requirements = data.get("requirements", "")
    languages = data.get("languages")
    is_open = data.get("is_open")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or "\x00" in name:
        raise ValueError("请填写数据结构名称，最多 100 字")
    if not isinstance(requirements, str) or len(requirements) > 5000 or "\x00" in requirements:
        raise ValueError("实现要求最多 5000 字")
    if (not isinstance(languages, list) or not languages
            or any(not isinstance(language, str) or language not in CODE_LANGUAGES for language in languages)):
        raise ValueError("请至少选择一种有效的代码语言")
    if not isinstance(is_open, bool):
        raise ValueError("请指定是否开放提交")
    return {
        "name": name.strip(), "requirements": requirements.strip(),
        "languages": list(dict.fromkeys(languages)), "is_open": is_open,
    }


@api_login_required
def collections(request, collection_id=None):
    membership = membership_for(request)
    if not membership:
        return api_error("未加入当前团队", 403)
    items = DataStructureCollection.objects.filter(team=membership.team, deleted_at__isnull=True)
    if request.method == "GET":
        scope = request.GET.get("scope", "team")
        if error := structure_read_error(membership, scope):
            return error
        if collection_id is None:
            count = Count("implementations", filter=visible_implementations(scope, membership, "implementations__"))
            summaries = items.annotate(entry_count=count)
            if scope == "mine":
                summaries = summaries.filter(entry_count__gt=0)
            if scope == "browse":
                own = Q(implementations__member_id=membership.pk)
                summaries = summaries.annotate(
                    own_published=Count("implementations", filter=own & Q(implementations__is_rejected=False)),
                    own_rejected=Count("implementations", filter=own & Q(implementations__is_rejected=True)),
                )
            result = []
            for item in summaries.order_by("position", "-id"):
                data = collection_data(item, item.entry_count)
                if scope == "browse":
                    data["my_submission_status"] = (
                        "submitted" if item.own_published else "rejected" if item.own_rejected else "unsubmitted"
                    )
                result.append(data)
            return JsonResponse({
                "collections": result,
                "team_id": membership.team_id,
                "languages": CODE_LANGUAGES,
                "can_manage": membership.is_admin,
            })
        item = items.filter(pk=collection_id).first()
        if item is None:
            return api_error("数据结构不存在", 404)
        entries = item.implementations.filter(visible_implementations(scope, membership)).select_related("member").defer(
            "code", "blog", "notes",
        )
        can_submit = bool(
            membership.pk and not request.user.is_superuser and membership.team.data_structures_enabled and item.is_open
        )
        semester = active_semester(item.team)
        return JsonResponse({
            "collection": collection_data(item, len(entries)),
            "implementations": [implementation_data(entry, membership) for entry in entries],
            "languages": CODE_LANGUAGES,
            "can_manage": membership.is_admin,
            "can_submit": can_submit,
            "training_rules": {
                "submissions": semester.data_structure_valid_submissions if semester else DATA_STRUCTURE_VALID_SUBMISSIONS,
                "code_points": semester.data_structure_code_points if semester else DATA_STRUCTURE_CODE_POINTS,
                "blog_points": semester.data_structure_blog_points if semester else DATA_STRUCTURE_BLOG_POINTS,
                "available": bool(can_submit and current_training_week(semester, timezone.now())),
            },
        })
    allowed = ("PATCH", "DELETE") if collection_id is not None else ("POST",)
    if request.method not in allowed:
        return api_error("不支持该请求方法", 405)
    if not membership.is_admin:
        return api_error("仅管理员可以管理数据结构", 403)

    if request.method != "DELETE":
        data = body(request)
        if collection_id is None:
            team_id = data.get("team_id")
            if type(team_id) is not int:
                return api_error("请刷新页面后再新增数据结构")
            if team_id != membership.team_id:
                return api_error("当前团队已切换，请刷新页面后再新增数据结构", 409)
        elif type(data.get("version")) is not int:
            return api_error("请重新打开编辑窗口后再保存", 409)
        try:
            fields = collection_fields(data)
        except ValueError as exc:
            return api_error(str(exc))
    if collection_id is None:
        with transaction.atomic():
            Team.objects.select_for_update().get(pk=membership.team_id)
            last_position = items.aggregate(last=Max("position"))["last"]
            item = items.create(team=membership.team, position=(last_position or 0) + 1, **fields)
        return JsonResponse(collection_data(item, 0), status=201)

    with transaction.atomic():
        if request.method == "DELETE":
            Team.objects.select_for_update().get(pk=membership.team_id)
        item = items.select_for_update().filter(pk=collection_id).first()
        if item is None:
            return api_error("数据结构不存在", 404)
        if request.method == "DELETE":
            if item.implementations.exists():
                return api_error("已有代码提交，无法删除", 409)
            item.deleted_at = timezone.now()
            item.save(update_fields=["deleted_at"])
            return JsonResponse({"ok": True})
        if data["version"] != item.version:
            return api_error("数据结构设置已更新，请关闭并重新打开编辑窗口", 409)
        for field, value in fields.items():
            setattr(item, field, value)
        item.version += 1
        item.save(update_fields=[*fields, "version"])
        return JsonResponse(collection_data(item, item.implementations.count()))


@api_login_required
def reorder_collections(request):
    if request.method != "PATCH":
        return api_error("仅支持 PATCH", 405)
    membership = membership_for(request)
    if not membership or not membership.is_admin:
        return api_error("仅管理员可以调整数据结构顺序", 403)
    data = body(request)
    before, after = data.get("previous_ids"), data.get("ids")
    for ids in (before, after):
        if not isinstance(ids, list) or any(type(pk) is not int or pk <= 0 for pk in ids):
            return api_error("数据结构顺序无效")
        if len(ids) != len(set(ids)):
            return api_error("数据结构顺序无效")
    if set(before) != set(after):
        return api_error("排序必须包含当前团队的全部数据结构")

    with transaction.atomic():
        # Creation and deletion acquire the same team lock before changing this list.
        Team.objects.select_for_update().get(pk=membership.team_id)
        items = DataStructureCollection.objects.filter(team_id=membership.team_id, deleted_at__isnull=True)
        by_id = {item.pk: item for item in items}
        if list(by_id) != before:
            return api_error("数据结构列表已变化，请重新加载后再排序", 409)
        if after != before:
            for position, item_id in enumerate(after, start=1):
                by_id[item_id].position = position
            DataStructureCollection.objects.bulk_update(by_id.values(), ["position"], batch_size=500)
    return JsonResponse({"ok": True})


def visible_implementations(scope, membership, relation=""):
    if scope == "manage":
        return Q()
    published = Q(**{f"{relation}is_rejected": False})
    own = Q(**{f"{relation}member_id": membership.pk})
    if scope == "mine":
        return own
    if scope == "browse":
        return published | own
    return published


@api_login_required
def implementations(request, collection_id, implementation_id=None):
    membership = membership_for(request)
    if not membership:
        return api_error("未加入当前团队", 403)
    items = DataStructureCollection.objects.filter(team=membership.team, deleted_at__isnull=True)
    if request.method == "GET" and implementation_id is not None:
        scope = request.GET.get("scope", "team")
        if scope == "history":
            if not membership.pk:
                return api_error("代码实现不存在", 404)
            entries = structure_history_rows(membership)
        else:
            if error := structure_read_error(membership, scope):
                return error
            entries = DataStructureImplementation.objects.filter(
                visible_implementations(scope, membership), collection__in=items,
            )
        item = entries.filter(collection_id=collection_id, pk=implementation_id).select_related(
            "member", "training_week__semester",
        ).first()
        if item is None:
            return api_error("代码实现不存在", 404)
        response = JsonResponse(implementation_data(item, membership, include_code=True))
        if scope == "history":
            response["Cache-Control"] = "private, no-store"
        return response

    allowed = ("PATCH",) if implementation_id is not None else ("POST",)
    if request.method not in allowed:
        return api_error("不支持该请求方法", 405)
    data = body(request)
    request_id = None
    if implementation_id is None and "request_id" in data:
        try:
            request_id = UUID(str(data["request_id"]))
        except ValueError:
            return api_error("提交请求编号无效")
    received_at = timezone.now()
    week = None
    if implementation_id is not None:
        semester_id = DataStructureImplementation.objects.filter(
            collection__in=items, collection_id=collection_id, pk=implementation_id,
        ).order_by().values_list("training_week__semester_id", flat=True).first()
    else:
        if membership.pk and not request.user.is_superuser:
            week = current_training_week(active_semester(membership.team), received_at)
        semester_id = week.semester_id if week else None
    with transaction.atomic():
        semester = Semester.objects.select_for_update().filter(pk=semester_id).first() if semester_id else None
        # Follow configuration's semester-before-team order, then lock the collection.
        team = Team.objects.select_for_update().filter(pk=membership.team_id).first()
        if team is None:
            return api_error("当前团队不存在", 404)
        collection = items.select_for_update().filter(pk=collection_id).first()
        if collection is None:
            return api_error("数据结构不存在", 404)
        item = None
        if implementation_id is not None:
            item = collection.implementations.filter(pk=implementation_id).select_related("member").first()
            if item is None:
                return api_error("代码实现不存在", 404)
            action = data.get("action")
            if action == "reject" and not membership.is_admin:
                return api_error("仅管理员可以驳回提交", 403)
            if action != "reject" and item.member_id != membership.pk:
                return api_error("只能修改自己的代码", 403)
            if "version" in data and data["version"] != item.updated_at.isoformat():
                return api_error("提交已变化，请刷新后重试", 409)
            if action == "reject":
                if item.is_rejected:
                    return api_error("该提交已被驳回", 409)
                note = data.get("rejection_note")
                if not isinstance(note, str) or not note.strip() or len(note) > 1000 or "\x00" in note:
                    return api_error("请填写驳回原因，最多 1000 字")
                item.is_rejected = True
                item.rejection_note = note.strip()
                item.save(update_fields=["is_rejected", "rejection_note", "updated_at"])
                update_training_credit(item, semester)
                return JsonResponse(implementation_data(item, membership))
            if item.is_rejected != (action == "resubmit"):
                return api_error("提交状态已变化，请刷新后重试", 409)
        if not membership.pk or request.user.is_superuser:
            return api_error("请以团队成员身份提交代码", 403)
        if request_id:
            previous = collection.implementations.filter(member=membership, request_id=request_id).first()
            if previous:
                blog = data.get("blog", "")
                if (data.get("language") != previous.language or data.get("code") != previous.code
                        or not isinstance(blog, str) or blog.strip() != (previous.blog or "")
                        or not isinstance(data.get("notes", ""), str)
                        or data.get("notes", "").strip() != previous.notes):
                    return api_error("这次提交已保存，请打开已有记录查看或编辑", 409)
                return JsonResponse(implementation_data(previous, membership))
        if not team.data_structures_enabled:
            return api_error("当前团队已关闭数据结构", 403)
        if not collection.is_open:
            return api_error("该数据结构已关闭提交", 409)
        language = item.language if item else data.get("language")
        if language not in collection.languages:
            return api_error("请选择允许提交的代码语言")
        code = data.get("code")
        notes = data.get("notes", item.notes if item else "")
        blog = data.get("blog", item.blog if item else None)
        if not isinstance(code, str) or not code.strip() or len(code) > 200000 or "\x00" in code:
            return api_error("请填写有效代码，最多 200000 字")
        if not isinstance(notes, str) or len(notes) > 5000 or "\x00" in notes:
            return api_error("实现说明最多 5000 字")
        if "blog" in data:
            if not isinstance(blog, str) or len(blog) > 100000 or "\x00" in blog:
                return api_error("Blog 最多 100000 字")
            blog = blog.strip()
        if item is None:
            record_date = timezone.localdate(received_at)
            if (semester and not semester.archived_at and semester.is_active
                    and semester.starts_on <= record_date <= semester.ends_on):
                week = semester.weeks.filter(
                    pk=week.pk, is_closed=False, starts_at__lte=received_at, ends_at__gt=received_at,
                ).first()
            else:
                week = None
            training_rules = {
                "training_rule_submissions": semester.data_structure_valid_submissions,
                "training_rule_code_points": semester.data_structure_code_points,
                "training_rule_blog_points": semester.data_structure_blog_points,
            } if week else {}
            item = collection.implementations.create(
                member=membership, language=language, code=code, notes=notes.strip(), blog=blog,
                training_week=week, training_date=record_date if week else None, request_id=request_id,
                **training_rules,
            )
            update_training_credit(item, semester)
            return JsonResponse(implementation_data(item, membership), status=201)
        item.code = code
        item.notes = notes.strip()
        item.blog = blog
        item.is_rejected = False
        item.rejection_note = ""
        item.save(update_fields=["code", "notes", "blog", "is_rejected", "rejection_note", "updated_at"])
        update_training_credit(item, semester)
        return JsonResponse(implementation_data(item, membership))
