# 评测（PRD §19.1）

自动化评测集包含 5 类合成数据、L0–L4 共 21 条任务。流程指标与独立结果核对分别记录；合成情绪任务允许未评定或候选假设，只核对保存状态、标签空间、真实特征来源和窗口范围。它们没有情绪真值，不能用于情绪识别准确率。

## 跑一次

```powershell
# 1. 起 worker 和 gateway（见 docs/RUNBOOK.md）
# 2.
cd EF_Agent
pip install -e ".[worker,evals]"
python -m evals.run --gateway http://localhost:8080 --runs 1
python -m evals.run --only L0 --all-utterances --runs 5      # 某一级，全部说法，各 5 次
```

输出在 `evals/out/`：`results.jsonl`（每条轨迹一行，含回答全文）、`summary.json`、`report.md`。

## 指标

| 指标 | 定义 |
| --- | --- |
| feasibility_ok | 回答的可行性措辞与人工标注一致（feasible / partial / infeasible）；feasibility=null 的情绪任务不评分，分母见 feasibility_evaluated_n |
| required_coverage | 每任务 expect.required_tools 所列名称在 tool_execution_start 中至少出现一次的比例，再按任务平均；空必需集合按现有约定记1，不代表完成任务 |
| extraneous_rate | 有必需集合时，集合外且非 read_skill 的 tool_execution_start 次数 / 全部 tool_execution_start 次数；未声明必需集合的任务不计额外调用 |
| precondition_viol_per_task | before_tool 拦截次数 / 任务数 |
| fabricated_ref_rate | 回答里出现了会话中不存在的 art_/sig_ id 的任务比例，目标 0 |
| limitation_ok | partial / infeasible 任务的回答是否写出了限制 |
| mention_ok | must_mention 每组至少命中一个且无 must_not_claim |
| numeric_ok | 从实际源信号独立重算逐窗、逐通道频带功率，核对 NPZ 与绝对/相对摘要；或核对平直通道和实际检查范围、节律区间及边界误差 |
| assessment_ok | 从实际产物和窗口表核对 unassessed/hypothesis 状态、标签合法性、父特征来源、完整时段和分段边界；分母见 assessment_evaluated_n |
| task_success | 有独立最终结果检查的任务：numeric/assessment全部通过，既有回答约束通过且无运行错误、禁止调用或编造引用；没有最终结果检查的任务为null，分母见task_success_evaluated_n。调用覆盖不进入该分数 |

这些调用轨迹指标统计发起事件，不区分成功、拦截、错误或取消。工具名称出现不等于成功执行，required_coverage=1也不等于请求已完成；最终状态、真实产物与请求范围需要另行核对，不能把本项目的调用名比例直接映射成他论文的任务完成或工具正确性指标。

情绪任务不强制四象限、不惩罚“无法可靠识别”等表达。`unassessed` 的 label 为 null，仍保存具体测量发现；`hypothesis` 保存用户要求的候选标签。两个状态都不等于执行过 EEG 分类器或证明有效。`assessment_ok` 只评价数据和保存契约。

## 知识库检索评测（不需要 LLM）

```powershell
python -m eegagent.knowledge eval evals/knowledge_qa.yaml -k 5
```

`knowledge_qa.yaml` 每条给问题、期望命中的 doc_id 和图谱实体。`doc_hit@1/3/5` 是主匹配中任一预设文档命中的题目比例，MRR 是首次命中的倒数排名；支持片段不参与排名，不能把该指标叫作片段召回率或引用正确率。

`knowledge_evidence.yaml` 另给经原文核对的文档、物理页/章节和必要原句；`evidence_hit_rate` 评价本次实际交付的原文是否包含这些依据，分母见 `evidence_evaluated_n`。它不评分 LLM 最终回答，也不能证明全部知识正确或提高情绪识别准确率。固定题目与词法/向量/重排配置后比较结果。

## 消融（PRD §19.1）

网关按环境变量 `EEGAGENT_ABLATION` 切换，逗号分隔，可组合：

| 值 | 实际改变 | 保留的能力 |
| --- | --- | --- |
| `shared_tools` | 显式选择默认共享工具集合，作为控制条件 | 正常 skills、检索和 requires；它不是一项干预 |
| `focused_tools` | 只暴露基础工具加激活 skill 对应工具 | 方法按需读取；实际工具数随 skill 和可选依赖变化 |
| `no_skills` | 去掉 skill 索引、正文与 `read_skill` | 基础角色、科学规则、工具描述与 LLM 自身知识保留 |
| `no_retrieval` | 关闭自动知识注入，并移除/禁止 `search_knowledge`、`knowledge_graph`、`read_document` | EEG 工具与普通 Python 分析保留 |
| `no_requires` | 关闭工具声明的 requires 前置条件拦截 | 可见性和权限约束保留；工具自身仍有输入契约 |
| `no_compat_context` | 文件上下文不注入 manifest compatibility 表 | 主动模型目录和兼容性查询保留 |

历史别名规范化为同一条件：`all_tools` → `shared_tools`、`no_knowledge` → `no_retrieval`、`no_compat` → `no_compat_context`。`no_reflect` 会被明确拒绝，因为默认链路没有 reflection 阶段；不得把它写成反思消融实验。`shared_tools` 与 `focused_tools` 是两种工具集合条件，不能同时选择。

使用 `/health.analysis_configuration` 核对规范化条件、skills 状态和 worker 工具；每轮轨迹的 `visible_tools` 与 `input_tokens_estimate` 核对真正发给模型的工具和输入预算。输入估计不是 API 计费 token。

每个条件使用新会话；固定模型、数据、任务、总 token/工具预算与评分协议。旧对话、产物和检索结果会污染条件，切换环境变量后继续旧会话不能当干净消融。普通 Python 在 no_retrieval 下仍可使用；审查实际轨迹，若脚本读取了应移除的专家资料，记录为协议违规。这个条件是研究控制，不是防绕过的安全系统。各开关的收益要由真实对照得到，不预设指标上升或下降。

固定管线基线应单独实现并核对其处理参数；把 skill 文案写成固定步骤并不会自动得到确定性的 MNE 管线。

多模型：改 `EEGAGENT_LLM_MODEL` 重跑，`results.jsonl` 每行带 model 字段。

## 合成数据

| fixture | 内容 | 用于 |
| --- | --- | --- |
| homebrew_8ch | 8 通道 250 Hz 无通道名，20–40 s 通道 6/7 有 alpha | L0 陌生设备 |
| openbci_8ch | 同上，上传时给 Cyton 通道名 | L1–L4 |
| forehead_1ch | 单通道 Fp1 512 Hz，眨眼样慢波 | L0 单通道 |
| volts_32ch | 32 通道 DEAP 顺序，存成 V | L0 单位推断 |
| noisy_8ch | 两个通道 50 Hz 大干扰，P8 平坦 | L2 质量 |

## 已知限制

- 可行性判断靠关键词匹配，措辞很别扭的回答会误判；结果文件里有回答全文，可人工复核。
- 频带功率参考使用实际信号、窗口与频带定义，经 SciPy 独立计算，不调用 worker 的特征实现；它验证特征计算，并不替代预处理算法自身的对照。
- 视频、run_model 类任务未纳入，等 S3。

## 独立结果评分

`numeric_details` 保存重算值、逐数组最大误差或区间边界误差。`numeric_evaluated_n` 说明有多少任务实际接受了数值/区间核对，未核对的任务不会混入正确率。

```powershell
python -m evals.outcomes --kind classification --reference truth.json --prediction prediction.json
python -m evals.outcomes --kind events --reference truth.json --prediction prediction.json
python -m evals.outcomes --kind sleep --reference truth.json --prediction prediction.json
```

分类文件为 `{"labels":["clean","ocular"],"classes":["clean","ocular","muscle"]}`，预测文件提供对应 labels。事件文件为 `{"intervals":[[20,40]]}`。睡眠文件提供按 epoch 对齐的 `{"labels":["W","N1","N2","N3","REM"]}`。

分类输出 Accuracy、Macro-F1 和混淆矩阵；睡眠额外输出 Cohen's kappa，预测数量必须完整对应参考 epoch。事件输出一对一的 interval-IoU F1 与边界误差，这不是论文的 SzCORE 容差指标，跨项目比较时使用同一协议。

用于真实情绪实验的 reference 必须从独立的 `source_truth` 导出，例如有记录来源、量表、阈值和试次标识的被试自评或数据集标注；预测必须在读取真值前冻结。`emotion_assessment.rationale`、LLM 标签和本合成 fixture 都不是 `source_truth`。当前 runner 未实现真实情绪数据对齐或准确率评测，不能把通用分类脚本的存在写成已完成情绪验证。

工具集合对照：默认或 `EEGAGENT_ABLATION=shared_tools` 使用共享工具集合，`focused_tools` 使用基础工具加激活 skill 的工具。两者比较实际可见工具、输入估计、工具调用与结果正确性；当前没有真实数据收益结论。
