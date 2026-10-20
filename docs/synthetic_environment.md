# Synthetic Hospital Environment

## 固定事实 → 状态规则 → 对话 → 任务反馈

医院事实来自 `seeds/hospital.json`，就诊规则来自 `workflows.json`，患者初始状态来自 `patient_profiles.json`。运行时通过同一套 ID 关联。`linbot_voice_agent/data/seeds/` 是安装包副本，修改源文件后用 `scripts/sync_seeds.py` 同步，测试会检查一致性。

患者 01 已预约心内科但未报到。初始位置是入口。任务确认后最短路为入口→服务台→电梯厅→心内科；三次 `/tick` 后完成。虚拟跨层边代表模拟电梯连接，不证明机器狗具备乘梯能力。

## 完整门诊取药闭环

1. `/next` 或自然询问下一步，提出前往预约科室。
2. 用户确认，生成导航任务 ID。
3. 多次 `/tick`，完成事件更新位置与访问记录。
4. `/event checked_in`，报到成功。
5. `/event consultation_completed`，模拟医生接诊完成。
6. `/next` → 确认 → `/tick`，前往缴费处。
7. `/event payment_completed`，确认缴费。
8. `/next` → 确认 → `/tick`，前往药房。
9. `/event medication_collected`，完成当前流程。

`/event` 是测试操作员命令，不是患者语义或 LLM 工具。真实医院集成时应由受认证的医院系统/人工操作提供这些事件。

## 任务状态

`idle → running → completed`；running 可暂停到 paused，确认恢复后回 running；执行中可失败到 failed，或进入 emergency。取消发出 cancelled 事件后回 idle。失败与暂停不会伪造到达；重新导航产生新任务 ID。

异常目录有7种事件。`destination_closed` 会将目的地加入本次环境关闭集合，直到 `/reset`；`blocked` 只使当前任务失败。当前低电量等异常是注入事件，不是连续电量/物理模型。

## 合成数据

- 12 个患者由5条流程及预约科室、辅助需求等种子组合得到；都是虚构案例，不模拟疾病诊断。
- 12 个单轮种子覆盖下一步、导航、地点查询、暂停、取消、未知地点、情感等。
- 6 个多轮种子覆盖全流程、暂停恢复、改目的地、歧义、失败、紧急求助。
- LLM 扩写只变更可编辑的用户话语，明确确认词和模拟操作员事件保持不变。
- 扩写与真实模型评估都需要用户配置服务。无密钥时已有固定计划演示和 mock 回归测试可运行。

当前没有生成数千条问答；先确保少量种子的规则闭环，再按误判类别扩展表达。
