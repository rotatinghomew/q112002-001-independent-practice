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

使用 Python 3.11 标准库，无需安装包。用 `python3 record_reader.py` 检查并列出收到的表行数；也可传入同格式资料目录。用 `python3 -m unittest discover -v` 检查资料契约。读取入口不会计算分析结果。
