# 当前开发状态：v0.2.4 / local-deployment
新增本机启动入口：start-local.cmd、start-local.sh；默认mock不读.env，--live显式读取已配置API。网页、API、队列线程、SQLite和文件均本机；不等待GitHub/Render。
远程模拟入口：start-online-test.cmd，复用现有Cloudflare Quick Tunnel保护、独立8001端口和preview-data；无cloudflared时明确停止。没有创建公网入口或云资源。
先读docs/LOCAL_DEPLOY.md和docs/LOCAL_VALIDATION.md。当前已有依赖环境测试；Windows实机和干净依赖安装未验证通过。
业务下一任务仍DEV-002（互斥产品选项映射），不可因部署更改预算/范围；定向local测试，提交前全套。所有真实LLM/OCR/CAD调用0次。
