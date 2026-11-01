# 赛事中心、平台账号与奖励

## 个人空间

每个平台可绑定多个账号，分别同步 Rating、过题数和题目记录。支持编辑、解绑、全部同步和重新完整同步。排行榜取最高的当前 Rating；同平台过题去重，首次 AC 日期取最早记录。力扣中国站使用 `cn:` 前缀，与国际站分别统计。

Codeforces 可填写 API Key / Secret；牛客和 LeetCode 可填写 Cookie；QOJ 使用用户名和 Cookie。凭据按账号加密保存，编辑时留空保留，支持替换和清除。洛谷使用公开账号或 UID，管理员查询 Cookie 继续用于逐题 AC 时间查询。

注册选择入学年份，年级由入学年份和当前学期计算，分队与称号沿用现有功能。奖励配置按入学年份对应的分队分别设置。

## 赛事日程与补题 Tracker

赛事日程提供 Codeforces、AtCoder、牛客、洛谷、LeetCode 的比赛快照；“已参加”展示历史参赛进度和领奖入口。

补题 Tracker 提供 ICPC/CCPC、Codeforces、AtCoder、牛客四类比赛矩阵。支持搜索、全部账号或单账号切换，ICPC/CCPC 增加全部、ICPC、CCPC、省赛分类。每页 20 场，点击题号打开原题，窄屏可横向滚动。

目录来源：QOJ 分类与比赛页、Codeforces 官方常规比赛 API、AtCoder Problems、牛客官方 Tracker。ICPC/CCPC 使用 XCPCIO 与 RankLand 公开榜单匹配金银铜铁分档。QOJ 登录后取得的题目详情按账号保存。

日程和 Tracker 目录每天北京时间 06:00、18:00 更新，页面也可刷新目录、同步账号。

## 管理员奖励配置

在“管理工作台 → 团队配置 → Coins 规则”中，按分队（入学年份）分别设置每日签到、自然周连签、各平台赛内 AC 与补题 AC 金额。平台题目奖励分三档；QOJ 使用统一单题金额，赛内和补题默认每题 2 Coins。其他平台补题默认 0，赛内沿用既有金额。配置 0 关闭对应奖励。

| 平台 | 第二 / 第三档起点（大一上） | 第二 / 第三档起点（其余年级） |
|---|---:|---:|
| LeetCode | 600 / 1200 | 1000 / 1500 |
| Codeforces | 1000 / 1600 | 1400 / 2000 |
| AtCoder | 400 / 1000 | 800 / 1600 |
| 牛客 | 1100 / 1600 | 1600 / 2100 |
| 洛谷 | 2 / 4 | 4 / 6 |

签到、连签、赛内 AC 和补题 AC 均使用成员入学年份对应分队的金额；题目难度阈值沿用作答或比赛时的年级规则。赛内奖励沿用参赛与时间核验规则，奖励起点为北京时间 2026-11-01。补题使用已同步的具体题目 AC 记录；已取得日期的记录从该日起参与奖励。

个人空间和 Tracker 的补题领奖、比赛页领奖共用凭证；同团队同用户同平台同题、同平台账号同题均只发一次。发奖、余额和流水在同一事务中更新，解绑和重绑不会重置领奖凭证。

## 升级与接口

执行迁移 `0041`–`0043`，重新构建并重启 Web、Worker、Beat。迁移保留现有绑定、历史关联及领奖记录，后续同步补全题目记录。

- `POST /api/profile/platforms/{platform}/accounts`：添加账号。
- `PATCH/DELETE /api/profile/platform-accounts/{id}`：编辑、解绑。
- `POST /api/profile/platform-accounts/sync`：按账号 ID 列表同步，可指定完整同步。
- `GET /api/trackers/{kind}`：读取分页比赛矩阵。
- `POST /api/trackers/{kind}/refresh`：刷新目录。
- `POST /api/profile/platform-rewards/claim`：领取补题奖励。

旧平台绑定接口保留单账号兼容。多账号凭据使用现有系统密钥，升级时保留环境配置和密钥。

## 参考与许可

平台同步与补题流程参考 OJ Insight Personal，许可见 [OJ Insight MIT License](third-party/OJ-Insight-LICENSE.txt)。
