# 公开资料与难度复用

`club/public_catalog.py` 是公开来源的统一读取入口。比赛发现、手动链接补录、比赛核验和 Tracker 通过 `PublicRemote` 使用它；比赛日历通过 `calendar_text` 使用同一个入口。同一 URL 的参数排序后生成相同缓存键，JSON 来源不因调用者使用文本或 JSON 接口而重复下载。

## 复用范围

- Codeforces：比赛目录、题库与 rating。
- AtCoder：比赛、题目、比赛题目对应关系、难度模型、官方比赛 tasks 页面。核验继续以官方 tasks 为准，避免复用题被 AtCoder Problems 重归属后漏题。
- 牛客：比赛信息、比赛题目分页、Tracker 分页。Tracker 与奖励难度查询统一使用每页 100 条。
- LeetCode：比赛信息和列表、zerotrac 难度文件。中国站和国际站 URL 分开缓存。
- 洛谷：需要 Cookie 的比赛详情不进入公共缓存；已验证的题目难度可以进入公共难度表。
- QOJ：沿用既有流程和奖励策略。

不会将个人提交、参赛历史、榜单、GraphQL POST 或带凭据的响应放入公共缓存。不同来源的目录并不互相替代：例如 AtCoder 官方日历用于未来赛事，AtCoder Problems 用于历史目录。

## 缓存与失败处理

使用现有 `platform_requests` Redis 缓存，跨 web/worker 进程复用。比赛信息一般有效 15 分钟，大型目录和难度来源 1 小时；已结束超过 7 天的 AtCoder tasks 页面有效 1 天。保留最近一次成功快照 30 天（其中包含来源 URL、原始内容和获取时间），到期必须重新获取。

同一来源刷新有 180 秒互斥租约；等待者最多等待 12 秒，然后明确返回“正在更新”。释放租约使用 Redis 原子校验，避免旧请求释放新请求的锁。网络失败或可识别的异常响应不覆盖旧快照，过期快照也不会冒充新证据。已有日历和比赛进度继续由原来的数据库快照保留。Redis 数据丢失只导致重新获取，不影响金币账本。

## 共享难度

迁移 `0070` 新增 `SharedProblemDifficulty`，按平台和题目身份保存已知难度、来源和更新时间。原始平台值保留在上述来源快照中，统一换算函数负责 AtCoder 低分修正、牛客等级到 rating 的映射。AtCoder 使用未取整值判断奖励阈值，Tracker 与奖励保持一致。

成功的 Tracker 批次和成功核验的比赛题目更新该表；刷新缺失难度不会清空已有已知值。题目别名通过既有 Tracker 身份映射合并，并选取最近更新的共享值。普通奖励、比赛奖励和 Tracker 展示读取同一个元数据入口。响应提供 `difficulty_known`、`difficulty_source`、`difficulty_updated_at`；没有共享记录时保留既有核验值或目录值。

该表逐步随现有刷新任务填充，不额外添加定时任务，也不批量请求外站做初始化。已领取金额仍以领取记录和账本为准，不重发、不撤回。未知难度和 QOJ 奖励保持原规则。每天北京时间 04:00 的比赛查询与手动领取流程不变。

## 验证

隔离外站缓存的回归测试入口：

```text
python manage.py test club.test_public_catalog club.test_contest_time_window club.test_submission_evidence club.test_contest_sync_closure club.test_ac_evidence club.test_contest_progress club.test_contests club.test_contest_nightly --testrunner club.test_public_catalog.CatalogTestRunner
```

测试涵盖跨消费者复用、并发合并、异常刷新保留、账号请求不缓存、地区隔离、难度别名、统一奖励取值，以及已有比赛时段和自动查询规则。
