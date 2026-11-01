# 头像尺寸、历史兼容与缓存

使用现有 Pillow 和 Django storage，无新增依赖、表或迁移。上传限制仍为 PNG/JPEG/WebP、3 MB、1600 万像素；校正 EXIF 方向并移除元数据；仅等比缩小和压缩，不裁剪、不放大小图。新上传存储为 最长边不超过 512px 的等比 JPEG（quality 88、optimize），同时生成 最长边不超过 96px 的等比 JPEG（quality 82、optimize），分别用于个人资料与排行榜等小头像。

历史 `UserProfile.avatar` 路径、原文件和用户关系保持不变。缩略图存放在 `avatars/thumbnails/`，文件名由原头像文件名和缩略规则版本确定。缩略图缺失时，首次 small 请求生成后返回最长边不超过 96px 的图片，绝不回退下载原始高清图；原图损坏或丢失返回 404，界面显示姓名首字。原有不带 size 参数的链接继续返回个人头像。

批量处理默认只预览：

```powershell
docker compose exec -T web python manage.py build_avatar_thumbnails
docker compose exec -T web python manage.py build_avatar_thumbnails --apply --batch-size 100
# 可选：只处理一个账号
docker compose exec -T web python manage.py build_avatar_thumbnails --apply --user-id 2
```

仅补缺失缩略图，可重复执行；逐账号加锁，与头像更换互斥。错误逐条报告，保留原数据。新头像更换后，通过既有 FileDeletion 清理旧原图与对应缩略图。部署前保留现有媒体备份；批量命令不覆盖历史原图。

API 提供 `?size=small&v=<版本>`；个人页使用 `size=large`。版本包含原头像存储路径及处理规则，正常上传使用 UUID 文件名，更换后 URL 自动变化。排行榜批量查询头像信息，不逐行查询用户资料；无头像返回 null，前端不发送无意义的图片请求。所有客户端时间戳缓存破坏参数已移除，更换头像只更新当前成员。

版本匹配的响应使用 `private, max-age=86400, immutable`；旧链接或过期版本使用 `private, no-cache`，支持 ETag/If-None-Match 和 HEAD。权限校验在 304 判断前执行，响应 Vary: Cookie。头像为登录且团队可见数据，因此明确发送 CDN-Cache-Control/Surrogate-Control: no-store，不开放 CDN 公共缓存；CDN 配置应继续遵守这些响应头，不为 `/api/` 强制“缓存所有内容”。用户端重复渲染直接复用浏览器缓存，更换头像通过新版本 URL 获取新内容。

规则版本 jpeg-contain-v2 保留完整画面，界面使用 object-fit: contain。旧版本已裁剪的原图无法恢复被裁掉的像素，需要重新上传原图；历史仍完整的源文件补生成时不会裁剪。上传头像加载成功后移除默认文字，失败时恢复姓名首字，避免叠加或挤出第二行。
