# 当前开发状态：v0.2.1 / prototype-bootstrap
目标不变：全项目、全专业、四表、证据、人工审核。当前仅交付首个本地开发切片，不是完整商业产品。

已实现：FastAPI本地网页；SQLite持久化；4MiB分片续传和SHA-256去重；单活跃运行；TXT/DOCX正文/PDF文字和定位基础解析；来源与未处理状态；仅合成标记的零费用mock；文本DeepSeek适配器（仅MockTransport测试）；300CNY原子预算；暂停、日期比较基础规则；人工审核与JSON/XLSX导出。
未实现：真实API连通/施工精度验证、OCR/视觉/DWG、复杂版面关系、产品选项完整映射、几何Takeoff、完整跨专业冲突、生产认证/对象存储/数据库/任务队列、10GB与24h压测。当前运行最终显示PARTIAL。

默认不读 `.env` 或 `.local`，不付费。运行：`python -m app`。定向：`python scripts/run_checks.py --area api|gateway|parsers`；全量用 `--area all`。
下一任务：`python scripts/context_pack.py DEV-002`，完善已有Schema的互斥产品映射；不重新发明架构。DEV-003为PDF来源viewer；DEV-004为有用户授权后的API探针。
约束：按内部修订日期，不按上传；选项不双计；设计净量；QA不补外部正文；模型不能审批自己。大多数端到端产品需求仍planned；个别已实现代码不代表整个产品完成。精确结果见VALIDATION_REPORT。
