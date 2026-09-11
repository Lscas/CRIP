# 当前开发状态：v0.2.6 / evidence-verification
新增字段引用与独立核验，不改变原文、业务批准状态或预算。见docs/EVIDENCE_VERIFICATION.md与docs/EVIDENCE_VALIDATION.md。
定向：python scripts/run_checks.py --area evidence；全套：--area all。新表自动幂等创建，不删除.local/.env。GET不发送模型请求。
真实API未调用，mock只展示原文定位且保留待语义核验。全项目无漏项、原生CAD、OCR、准确率未验证。原DEV-002产品选项映射仍待开发，不以本轮字段核验冒充其实现。
