# DeepSeek文本适配器与真实接入门禁
**规格版本：** 0.2.1
当前 `app/gateway.py` 已实现OpenAI风格chat/completions HTTP接口；使用用户指定base URL，不包含秘密。只使用文本Flash，不直接输入PDF/图片，不自动升级。

## 手动配置
在本机复制.env.example为.env，按实际接口填写：
```text
CIRP_PROVIDER=deepseek
CIRP_LIVE_API_ENABLED=true
CIRP_API_BASE_URL=https://api.deepseek.com
CIRP_API_KEY=<只在本机填写>
CIRP_CHEAP_MODEL=deepseek-v4-flash
CIRP_PRICES_CONFIRMED=true
CIRP_INPUT_CNY_PER_MILLION=<实际接口输入最高适用单价>
CIRP_OUTPUT_CNY_PER_MILLION=<实际接口输出最高适用单价>
```
不要把示意占位符直接启用。API_BASE_URL为包含可选/v1的基础路径，程序追加/chat/completions。HTTPS且无URL用户名/密码/query/fragment；中转API需明确同意请求格式和usage。价格未确认、Key为空或开关关闭时，启动真实分析会暂停而非偷偷切换服务。

`python scripts/doctor.py`仅离线检查，不调用供应商。用户启动真实分析代表该次请求可能计费；先用小授权样例核实接口，不先上传整个10GB项目。应用没有已完成的自动能力探针或对账UI。

## 实际请求和保护
thinking=disabled，response_format=json_object，max_tokens<=2000。无高级模型路由；同片段最大3次显式重跑请求，未知账单未清时不重试。空/错JSON、截断、无证据、跨快照引用不发布；生成了思考内容则暂停以确认兼容性。真实请求可能计费，即使响应不合格。

发送前原子预留，usage按确认上限费率核销，不重复加reasoning_tokens。输入按UTF8字节保守估计并限量，非精确分词器；超限片段标待细分。缓存是版本化应用缓存，缓存命中零HTTP；供应商折扣不预先假定。

本轮HTTP测试全部MockTransport。没有真实Key、没有真实API成功声明、没有准确率/吞吐保证。图像/DWG先记录未处理，视觉接口单独接入任务，不能把图片交给文本模型冒充已读。
