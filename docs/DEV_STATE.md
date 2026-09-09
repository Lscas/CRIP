# 当前开发状态：v0.2.3 / render-free-preview
目标：免费云端只作测试，本机保留正式资料、真实API与实际运行。选择Render独立测试，无需本机一直在线。
新增：render.yaml显式free/单服务/手动部署；app/render_preview.py独立配置、mock、精确Host、PORT；无敏感信息健康检查；网页易失性警示/样例下载。云端测试100MiB/项目，不改变本机10GB目标。默认不读.env、不调用付费API。
状态：部署配置及测试已实现；实际云端发布未完成，Render未连接、连接中未找到CIRP源码仓库。FR-RENDER-LIVE-001 remains planned。不能拼接网址称已上线。
读取docs/RENDER_FREE_DEPLOY.md；定向`python scripts/run_checks.py --area render`；全量`--area all`。发布前用平台验收401/200/health/mock和Free费用边界。
业务下一任务继续DEV-002，使用context_pack，不重读整个仓库。OCR/视觉/DWG/Takeoff等未实现能力仍PARTIAL。代码回滚基线v0.2.2-cloudflare-preview。
