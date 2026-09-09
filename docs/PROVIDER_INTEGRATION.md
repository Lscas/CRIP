# DeepSeek与用户API接入契约
**规格版本：** 0.2.0

首选别名deepseek-v4-flash。L1使用非思考；L2同模型low思考；vision-exp只是已被官方文档列出的视觉候选，并未通过用户接口验证。[S1][S2] 低成本角色不意味着必须另训小模型。

## 接入前检查
确认用户提供的base URL、model ID、是否中转、每百万token价格、缓存命中价格、输出/推理计费口径、图像支持、OCR/CAD服务价格。只把Key写本地环境/Secret，不贴到文档/Git。

价格快照中的confirmed_for_user_endpoint=false，必须由实际接口信息确认后才能启用paid。若服务是第三方中转，官方能力和价格不能直接套用。没有外部OCR/CAD服务价格时，相应付费任务暂停，不能估0元。

## Chat Completions最小请求（设计示例，未执行）
```json
{
  "model": "deepseek-v4-flash",
  "thinking": {"type": "disabled"},
  "messages": [
    {"role": "system", "content": "只依据证据返回json，不输出思维链。"},
    {"role": "user", "content": "任务Schema和必要证据由服务端提供。"}
  ],
  "response_format": {"type": "json_object"},
  "max_tokens": 2000,
  "stream": false
}
```
这是HTTP JSON字段；若使用SDK，其扩展字段位置由adapter映射，不把上述JSON直接当任意SDK参数。思考模式默认开启，需显式关闭；JSON模式还需Schema和语义校验，空内容/截断不视为成功。[S1][S3]

## 能力探针结果
TEXT、JSON、THINKING_OFF、USAGE、VISION分别记录SUPPORTED/UNSUPPORTED/UNKNOWN，probe时间和供应商返回。测试只用合成文字/图片，小额度预留。文件解析由产品完成；不要发送PDF二进制给文本消息。Responses当前并非完整兼容所有内置文件工具，不依赖provider file_search。[S6]

## usage标准化
标准字段input_tokens、input_cached_tokens、output_tokens_total、reasoning_tokens_included、image_tokens_included、request_id、model_returned、finish_reason。总输出如已含推理不再重复收费。HTTP超时的charge_status=UNKNOWN，保持reservation；之后根据供应商账单或确定未计费证据核销。

## 禁用路径
不默认开启联网搜索，项目要求只基于上传文件；不默认发送邮件、生成采购订单、运行Shell或写CAD。没有视觉能力不能退回纯文本Flash声称完成视觉分析。当前包无实际API adapter，后续实现必须遵守这些合同。
