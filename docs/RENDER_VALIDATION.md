# v0.2.3 Render免费测试适配验证

**结果：代码与离线/本地HTTP检查完成；实际Render发布未完成。**

| 检查 | 实际结果 |
|---|---|
| 新增Render profile测试 | 29通过 |
| 完整Python回归 | 217通过，0失败、0错误、0跳过 |
| 原Cloudflare Worker回归 | 11通过 |
| JavaScript语法 / Python编译 | 通过 |
| 规格/Traceability/Prompt | 94项需求、12个Schema、8份Prompt；结构检查通过 |
| 真实loopback HTTP | health 200且只返回status；未登录401、登录200；模拟模式；示例下载200 |
| 平台创建 / 真实模型请求 | 0 / 0 |
| 实际服务URL | 未产生，不拼接网址 |

本轮使用Python 3.13.5。测试只使用合成输入、临时数据和随机临时密码，HTTP进程已停止；临时密码不写入源码、报告或最终包。

新增保护验证：缺少Host/密码拒绝启动；非Render域名及恶意Host拒绝；不继承.env、模型Key、本机资料路径或origin token；强制mock；只有/_health GET/HEAD公开；业务API鉴权与CSRF；云测试100MiB项目上限；本机10GB设置不变；Blueprint恰好一个明确free服务、无数据库/磁盘、自动部署关闭；平台生成密码而非硬编码。合成上传/分析/导出通过，输出5项且模型调用为0。

未验证：Render账户权限、平台Blueprint完整schema/API校验、平台构建、冷启动、公网TLS、Windows实机、免费工作区实际余额/计费设置、实际资源使用、持续运行和施工语义准确率。连接中未找到CIRP源码仓库；没有创建公开仓库或修改远端分支。没有执行付费API或采购资源。新增配置是免费测试适配，不是零账单或上线成功证明。

测试文件：`tests/app/test_render_preview.py`；记录：`reports/pytest-v0.2.3.xml`、`reports/test-run-v0.2.3.txt`、`reports/worker-tests-v0.2.3.txt`、`reports/render-loopback-v0.2.3.json`。
第一次定向测试发现TestClient构造器不接受auth参数，已改为标准Authorization头并重新运行全部测试；没有删除或跳过失败测试。

回滚代码：`v0.2.2-cloudflare-preview`。没有数据库迁移、已有部署或本机用户数据变更。
