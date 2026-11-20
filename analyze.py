"""命令行入口：冻结报告、当前报告、批次规划、试算。

示例：
  python3 analyze.py report --publication m1
  python3 analyze.py report --as-of 110
  python3 analyze.py batch --as-of 110
  python3 analyze.py whatif --as-of 110 --marks extra_marks.json
"""

import argparse
import json
from pathlib import Path

from record_reader import read_records
from reports import frozen_report, current_report, what_if, batch_digest


def _objectives(tables):
    return tables["objectives"]


def _publication(tables, name):
    for row in tables["minutes"]:
        if row["publication"] == name:
            return row
    raise SystemExit(f"未知 publication：{name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立练习观察账分析")
    parser.add_argument("--folder", default=None, help="资料目录（默认 records/）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="班级报告（冻结或当前）")
    p_report.add_argument("--publication", help="冻结截点编号")
    p_report.add_argument("--as-of", type=int, help="当前试算分钟")

    p_batch = sub.add_parser("batch", help="下次交叉复评批次规划")
    p_batch.add_argument("--as-of", type=int, required=True)

    p_whatif = sub.add_parser("whatif", help="叠加假设桥接评分试算")
    p_whatif.add_argument("--as-of", type=int, required=True)
    p_whatif.add_argument("--marks", required=True, help="假设评分 JSON 文件")

    args = parser.parse_args(argv)
    folder = Path(args.folder) if args.folder else None
    tables = read_records(folder) if folder else read_records()
    objectives = _objectives(tables)

    if args.command == "report":
        if args.publication:
            payload = frozen_report(tables, _publication(tables, args.publication), objectives)
        else:
            as_of = args.as_of if args.as_of is not None else 10**9
            payload = current_report(tables, objectives, as_of)
    elif args.command == "batch":
        payload = batch_digest(tables, objectives, args.as_of)
    else:
        extra = json.loads(Path(args.marks).read_text(encoding="utf-8"))
        result = what_if(tables, objectives, args.as_of, extra)
        payload = {
            "试算前": result.before,
            "试算后": result.after,
            "变化": list(result.changed_keys),
            "说明": result.note,
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
