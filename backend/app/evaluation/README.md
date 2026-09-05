# 离线评测

评测集包含 36 条中文样本：14 条结构化提取样本，以及 22 条召回、冲突、删除和跨用户隔离样本。

默认命令只读取已提交的千问预测快照，不访问真实 API，因此可以在 CI 中稳定复现：

```powershell
cd backend
uv run python -m app.evaluation.runner
```

当提取 Prompt 或模型版本变化时，可在本地显式刷新预测快照：

```powershell
uv run python -m app.evaluation.runner --refresh-predictions
```

提取评分会把模型输出交给生产环境的 `assess_candidate`，再核对最终可持久化候选的内容、类型和 `active/pending` 决策。召回评分直接调用生产环境的 `rank_memories`，并执行与 Repository 相同的用户和状态过滤。

`reports/baseline.json` 保留完整指标与失败样本。当前 5 个已知失败可归为三类：

- 千问把职业目标误标为敏感信息，并把一次性午餐内容当成长记忆。
- 纯关键词召回无法稳定理解“前端学习路线”和“番茄工作法”的语义关系。
- “从北京搬到上海”与旧居住地几乎没有字面重合，当前冲突匹配无法拦截。

这些失败是后续 Prompt、向量召回和冲突治理改动的回归基线，不应通过删除困难样本来提高指标。
