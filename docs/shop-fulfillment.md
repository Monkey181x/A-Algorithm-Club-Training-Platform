# 商品核销与派发

新增两类商品，现有普通商品、补签券和消耗品保持原有行为。

## 核销类

管理员在商品设置中选择“核销类（卡密）”。

- 库存来源：每行追加一条卡密。重复卡密不会重复入库。购买时预留，用户在背包核销后消耗并看到卡密。预留和已核销记录不会被追加操作覆盖。
- API 来源：购买后进入背包，核销时请求接口。接口失败不消耗物品；成功结果保留在用户背包中，重复核销不重复发卡。

API 配置示例：

```json
{
  "url": "https://your-service.example/issue",
  "method": "POST",
  "headers": {"Authorization": "Bearer YOUR_TOKEN"},
  "body": {"product": "example"},
  "response_path": "data.code"
}
```

支持 GET/POST、JSON 请求体、以点分隔的响应字段路径。接口需支持 `Idempotency-Key` 请求头：同一背包物品重试时使用固定编号，防止超时后重试重复发卡。没有实际服务配置时，不会请求外部接口。仅允许公开 HTTPS 地址，不跟随重定向；使用项目现有的地址校验和连接绑定逻辑。

卡密和 API 配置在数据库中加密保存，普通商品接口不返回这些信息。已核销卡密仅购买者可读取。API 配置保存后不会回显；编辑时留空保留原配置。

## 实物类

购买后背包显示“派发中”，管理员工作台出现“待派发商品”。管理员线下发放后点击“确认已派发”，背包显示“派送成功”。系统管理员可处理全部团队；团队管理员只能处理当前管理团队。提醒只在工作台展示，不发送外部消息。

管理员编辑实物类商品时可设置“限购数量”，0 表示不限购。按同一账号对同一商品的历史订单数量累计，已派送商品仍计入；新设置或降低上限后立即按历史订单判断。达到上限时商品半透明显示并加盖“已达购买上限”印章，购买按钮禁用。服务端在商品锁内再次核验，重复请求不会重复计数，并发请求也不能超过上限。迁移 `0033_shop_item_purchase_limit` 仅增加字段，旧商品默认不限购。

印章由内置图片模型生成，文件：`static/images/purchase-limit-stamp.png`。生成提示词：Create a single minimal Chinese rubber-stamp watermark for an ecommerce product purchase-limit overlay. Exact text must be “已达购买上限”, all six Chinese characters accurate, bold readable simplified Chinese, arranged in one horizontal line within a simple thin double-line rounded rectangular stamp border. Muted dusty red ink (#b95c65), flat monochrome, very subtle rubber-stamp ink texture, no other words, no icons, no seals, no shadows, no background, truly transparent alpha. Wide composition 3:1, tightly composed around the stamp with a small transparent margin, straight baseline, crisp readable at 180px display width. This is a small UI asset for an elegant pale rose storefront.

## 合并与上线

不要整份目录覆盖同学的最新源码。先在 Git 中合并这次修改与同学的新提交，解决冲突，再在测试环境验证。

上线前备份数据库和上传文件，保留服务器原有环境变量，尤其是 `SECRET_KEY`。加密数据依赖该密钥，换掉密钥会导致旧密文无法读取。

数据库增加迁移 `0032_shop_fulfillment`，依赖本分支已有的 `0031_submission_code_part`。合并时若同学也增加了迁移，需要检查迁移依赖并处理冲突，不能直接重命名后跳过检查。该迁移仅新增字段和表，不清空旧商品、余额或购买记录。

更新顺序：合并代码 → 备份 → 更新服务与数据库迁移 → 更新静态资源 → 检查购买、背包、核销、派发。保留原数据库、媒体目录和环境配置；不上传本机数据库、私钥或 `.env` 覆盖线上文件。

如需回滚旧代码，先下架新商品类型，保留新增表和履约数据，再处理代码版本；不要通过删除表回退。
