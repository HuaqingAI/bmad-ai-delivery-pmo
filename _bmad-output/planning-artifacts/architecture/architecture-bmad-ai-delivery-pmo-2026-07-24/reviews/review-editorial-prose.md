| Original Text | Revised Text | Changes |
|---------------|--------------|---------|
| producer 选择 semantic dependencies，但不做容易分叉的“semantic content hash” | producer 只纳入会影响投影结果的依赖文件，但所有文件统一按原始 bytes 计算 hash，不另造“语义 hash” | 明确 semantic 修饰的是依赖选择，而不是 hash 算法。 |
| 具体“affected”由 dependency manifest 计算 | 具体受影响的下游产物由 dependency manifest 计算 | 去掉中英文混合且指代不清的 “affected”。 |
| Panel refresh 不直接拥有 WDR projection 语义；它要求一个基于本轮 live sources、且 drift gate 通过的 canonical state-audit | Panel refresh 不解析 WDR projection；它只接受基于本轮实时源生成、且 drift gate 通过的 canonical state-audit | 用可执行动作替代抽象的“拥有语义”。 |
| 现有基线测试已通过……共 199 项 | 本轮运行的 8 组相关测试共 199 项，全部通过 | 明确这是本轮实际执行结果，而不是仓库全部测试的宣称。 |
