# Job Collector · 我的岗位库

个人岗位收集工具：本人在专用Chrome正常登录BOSS，程序有界采集，岗位和关注状态保存在自己电脑的SQLite。公开网页仅作为操作界面，不托管招聘账号或岗位数据。

这是公网测试候选；公共页面连接本机和第二使用者验收仍待完成。适用Windows x64和Chrome；UI/UX后续调整。

## 使用方式

1. 从本仓库Release下载配套Windows ZIP并完整解压，双击Start-Collector.cmd；启动不会采集。
2. 首次由本人在专用Chrome正常登录BOSS，在本机配对页取得码，再到操作网页连接。
3. 使用本机网页可直接查询。使用[公开网页](https://zengweiyi1223.github.io/job-collector/)前，在解压目录创建 `settings.json`，内容为 `{"public_origin":"https://zengweiyi1223.github.io"}`；正常停止并重新启动程序，复制新配对码连接。浏览器的本地网络权限由本人按提示允许。公网连接仍在验证中。
4. 每轮最多20次新岗位详情尝试、最多3批列表；已有ID不复查旧内容，失败也占尝试额度。不保证新增满20条或覆盖全部搜索结果。
5. 查询、展开JD、原链接和人工关注。区域、薪资和规模仅筛本地库；不关注仍保留在全部列表。
6. 双击同目录Stop-Collector.cmd结束服务；重启重新配对，数据保留。

详细操作见portable/使用说明.md。源码在app，静态网页在docs，启动器源文件在portable。源码仓库不含运行时，直接从源码运行需要相应Python/Node依赖；日常使用下载已核验的Release免安装包。

## 范围与数据

本版默认广州、数据分析师、全职；关键词可配置。仅BOSS，暂无岗位发布时间、旧详情更新、AI评分、投递或后台定时功能。岗位详情为采集时快照，最新招聘现状通过原链接由本人确认。验证码由本人正常处理后显式继续，其他访问限制停止；不绕过访问控制。

SQLite、Chrome配置和配对码仅本机使用，禁止提交或上传。每人独立解压运行，各自数据和登录配置独立。公开HTTPS网页连接本机时，使用精确来源配置与会话码，并由本人处理浏览器正常的本地网络权限提示。

## 引用与许可

动态字体模块和相关选择器机制参考BossHunter固定提交67a17420cbaa05608ac5d077cf4c5a50c4701678。引用部分适用PolyForm Noncommercial 1.0.0，详见app/LICENSE-BOSSHUNTER.txt和app/THIRD-PARTY-NOTICES.md。随包Python、Node及依赖有各自许可，保存在发行包licenses目录。没有为本项目整体额外指定开源许可证。
