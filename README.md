# 独立练习观察账

阶段教研会议的材料由作答、提示记录、量规和分项评分组成。A、B、C 是班级，甲、乙、丙是匿名评阅员。
`stage` 是教学阶段整数，`minute` 是同一阶段的统一分钟轴。提示只适用于本阶段的同一学生与目标。
桥接作答不属于学生表现：两位教师按各自班口径对同一锚作答评分。量规的 `bridge_min` 与 `gap_limit` 是该目标准入门槛：两班至少有这么多共同评阅；配对评分之差的极差超过 `gap_limit` 时，不能用该班对确认跨班结果。评分为 0 至 4。跨多对班级时，互相矛盾的尺度关系应标待复评，不选一条任意路径。最低独立次数指不同任务上的独立机会；`score_floor` 是区分辅导与达标的量规门槛。
教务回执是外部系统记录；已完成的回执号不会重复分配。`publication` 是会议采用的早期资料截止点，不是标准答案。补交、撤回用 `received` 表示到达分钟，撤回指向对应的桥接评分登记号。锚作答的共同分项使用 A 班量规作为跨班解释基准；不通向 A 时可保留本班分项观察，但不可视为使用 A 班尺度。复评容量按锚作答、分项计分钟，分项授权定义在轮次表。

## 资料格式

资料是匿名合成登记，不包含真实学生、真实成绩或外部访问地址。UTF-8 JSON，每张表是对象数组；`null` 表示该字段无已知值。`records/catalog.json` 记录每张表的字段。

- `objectives`：量规；goal 是目标编号。 字段：`goal`、`title`、`bridge_min`、`gap_limit`、`independent_min`、`score_floor`。
- `students`：教务负责老师的当前班级。 字段：`student`、`owner`。
- `attempts`：channel 为 independent、assisted 或 unresolved；independent 表示学生自报，仍需按提示记录核实。 字段：`attempt`、`student`、`room`、`goal`、`activity`、`stage`、`minute`、`channel`。
- `hints`：minute=null 表示已知给过提示但具体时刻未登记。 字段：`hint`、`student`、`goal`、`stage`、`minute`。
- `marks`：attempt 以 b 开头的是桥接作答，其余是学生作答。 字段：`mark`、`attempt`、`room`、`goal`、`rater`、`score`、`received`。
- `withdrawals`：桥接评分撤回通知。 字段：`notice`、`mark`、`received`、`reason`。
- `minutes`：会议早期采用的资料截点。 字段：`publication`、`cutoff`、`scope`。
- `receipts`：补评或辅导回执；state 为 complete 或 open。 字段：`receipt`、`student`、`goal`、`decision`、`owner`、`state`、`minute`、`publication`。
- `anchors`：可选锚作答及首次评阅员。 字段：`anchor`、`goal`、`from_room`、`first_rater`、`minutes`、`ready`。
- `review_rounds`：复评分项授权与剩余总分钟。 字段：`rater`、`room`、`goals`、`minutes`、`minute`。
- `assignments`：复评批次已接单或备发记录。 字段：`anchor`、`goal`、`rater`、`state`、`minute`。

## 本地查看

使用 Python 3.11 标准库，无需安装包。用 `python3 record_reader.py` 检查并列出收到的表行数；也可传入同格式资料目录。用 `python3 -m unittest discover -s tests -v` 检查资料契约。读取入口不会计算分析结果。

## 分析引擎

引擎只读登记表、不回写，全部按分钟轴重放，模块按业务规则分层：

- `observation.py`：时间快照与独立性判定。有确定时刻的提示只影响其后下一次作答；时刻未知的提示使该学生该目标该阶段全部作答不可作为独立证据；独立机会按不同 `activity` 计数，重做不增加机会，`unresolved` 不算作答。
- `scale.py`：跨班尺度桥接。撤回即时失效；按 goal 分别计算班对边，有效配对不足为覆盖缺口、差分极差超限为评委分歧；多路径偏移矛盾时标不可比并保留冲突路径，不任选一条。共同分项的可比性不外借到其他 goal。
- `dispositions.py`：复核单裁决与回执对账。依次区分补练（独立机会不足）、补评（缺可比较评阅）、辅导（可比但未达 `score_floor`）。已完成回执不复活为待办；裁决结论或依据指纹变化时登记更正事项，旧决定保留。跨班名次只排尺度连通的班，班内原始证据仍保留在评估结果中。
- `planning.py`：交叉复评批次。区分覆盖缺口与分歧缺口，校验授权（goal 名单、外班锚、非首次评委、未读过），容量按（锚、评委）只扣一次；已接单保留并占容量，备发可按新证据移除并写明后续核实；优先级按惠及的仍待确认学生数，而不是全校缺评分份数。规划只是补证承诺，评分到达前不参与尺度计算。
- `reports.py`：冻结报告、当前报告与试算。冻结报告按 publication 截点重放，会后撤回/补交不改写；`what_if` 叠加假设评分观察对仍待处理名单的影响，不落库、不复活已完成复核。

命令：

```bash
python3 analyze.py report --publication m1   # 会议冻结报告（截点90）
python3 analyze.py report --as-of 110        # 当前报告
python3 analyze.py batch --as-of 110         # 下次交叉复评批次
python3 analyze.py whatif --as-of 110 --marks extra_marks.json  # 试算
```

报告中每条建议都带 `依据` 与 `证据登记`（attempt、mark、撤回通知号），可直接追问结论是在哪一段证据上成立或断开的。
