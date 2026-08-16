"""查看按专利归并的 parquet 文件内容(只读查看工具,仅依赖 pyarrow)。

用法:
  python read_parquet.py                                   # 列出目录下所有 parquet 概要
  python read_parquet.py --patent WO2017021797             # 查看单个专利(全部视图)
  python read_parquet.py --patent WO2017021797 --view summary
  python read_parquet.py --patent WO2017021797 --view schema
  python read_parquet.py --patent WO2017021797 --view docs
  python read_parquet.py --patent WO2017021797 --view conversations --max-turns 20
  python read_parquet.py --patent WO2017021797 --view images
"""

import os
import argparse

import pyarrow.parquet as pq


def find_parquets(parquet_dir):
    """返回目录下按名称排序的 parquet 文件名列表。"""
    if not os.path.isdir(parquet_dir):
        print(f"[错误] 目录不存在: {parquet_dir}")
        return []
    return sorted(f for f in os.listdir(parquet_dir) if f.endswith('.parquet'))


def read_row(path):
    """读取 parquet(每个文件 1 行)并返回该行数据。"""
    table = pq.read_table(path)
    if table.num_rows != 1:
        raise ValueError(f"{path} 行数不为 1")
    return table.to_pylist()[0]


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024


def date_range(row):
    dates = [d["pub_date"] for d in row["docs"] if d["pub_date"]]
    return f"{min(dates)}~{max(dates)}" if dates else "-"


def print_list(parquet_dir, files):
    """列出所有 parquet 的概要。"""
    if not files:
        print(f"{parquet_dir} 中没有 parquet 文件")
        return
    print(f"{'专利号':<15} {'文件数':>4} {'页数':>5} {'图像':>5} {'对话轮数':>6} "
          f"{'日期范围':<19} {'大小':>8}")
    print("-" * 72)
    total_docs = total_pages = total_images = 0
    for name in files:
        path = os.path.join(parquet_dir, name)
        row = read_row(path)
        total_docs += row["n_docs"]
        total_pages += row["n_pages"]
        total_images += row["n_images"]
        print(f"{row['patent_no']:<15} {row['n_docs']:>4} {row['n_pages']:>5} "
              f"{row['n_images']:>5} {len(row['conversations']):>6} "
              f"{date_range(row):<19} {human_size(os.path.getsize(path)):>8}")
    print("-" * 72)
    print(f"共 {len(files)} 个专利 | {total_docs} 份文件 | {total_pages} 页 | {total_images} 张图")


def print_summary(row):
    print("\n【概要】")
    for k, v in [
        ("专利号", row["patent_no"]),
        ("申请号", row["application_no"]),
        ("公开号", row["publication_no"]),
        ("文件数", row["n_docs"]),
        ("页数", row["n_pages"]),
        ("图像数", row["n_images"]),
        ("对话轮数", len(row["conversations"])),
        ("日期范围", date_range(row)),
    ]:
        print(f"  {k}: {v}")


def print_schema(path):
    print("\n【列结构】")
    for i, field in enumerate(pq.read_table(path).schema, 1):
        print(f"  {i:>2}. {field.name:<16} {field.type}")


def print_docs(row):
    print("\n【各时期文件】")
    print(f"{'#':>3} {'seq':>4} {'zip名称':<26} {'kind':<5} {'日期':<9} "
          f"{'表单':<10} {'版本':<7} {'语言':<5} {'页数':>4} {'XML':>4}  错误")
    for i, d in enumerate(row["docs"], 1):
        print(f"{i:>3} {str(d['seq'] if d['seq'] is not None else '-'):>4} "
              f"{d['zip_name']:<26} {d['kind'] or '-':<5} {d['pub_date'] or '-':<9} "
              f"{d['form_type'] or '-':<10} {d['form_version'] or '-':<7} "
              f"{d['lang'] or '-':<5} {d['page_count']:>4} "
              f"{'是' if d['has_xml'] else '否':>4}  {d['error'] or ''}")


def print_conversations(row, max_turns):
    turns = row["conversations"]
    print("\n【对话内容】(ShareGPT 格式)")
    limit = len(turns) if not max_turns else min(max_turns, len(turns))
    for i, t in enumerate(turns[:limit], 1):
        lines = t["value"].split("\n")
        print(f"[{i:>4} | {t['from']:<5}] {lines[0]}")
        for line in lines[1:]:
            print(f"{'':>13} {line}")
    if limit < len(turns):
        print(f"  ...(共 {len(turns)} 轮,仅显示前 {limit} 轮;--max-turns 0 可显示全部)")


def print_images(row):
    print("\n【页面图像】(图像以字节内嵌,这里仅展示元数据)")
    print(f"{'#':>4} {'文件名':<34} {'页':>3} {'宽x高':<12} {'大小':>9} {'sha256前12位':<14} 来源TIFF")
    for i, (meta, b) in enumerate(zip(row["images"], row["image_bytes"]), 1):
        wh = f"{meta['width']}x{meta['height']}"
        print(f"{i:>4} {meta['path']:<34} {meta['page_no']:>3} "
              f"{wh:<12} {human_size(len(b)):>9} "
              f"{meta['sha256'][:12]:<14} {meta['source_tiff']}")
    if row["n_images"] != len(row["images"]):
        print(f"  [警告] n_images={row['n_images']} 与 images 列表长度 {len(row['images'])} 不一致")


def main():
    parser = argparse.ArgumentParser(
        description="查看按专利归并的 parquet 文件内容(只读,仅依赖 pyarrow)。")
    parser.add_argument("--dir", default="./patent_parquet",
                        help="parquet 所在目录 (默认 ./patent_parquet)")
    parser.add_argument("--patent", default=None,
                        help="指定专利号(逗号分隔多个);不传则列出目录全部概要")
    parser.add_argument("--view", default="all",
                        choices=["all", "summary", "schema", "docs", "conversations", "images"],
                        help="查看内容:all=全部,summary=概要,schema=列结构,"
                             "docs=各时期文件表,conversations=对话,images=图像表 (默认 all)")
    parser.add_argument("--max-turns", type=int, default=50,
                        help="conversations 最多显示的对话轮数 (0=全部,默认 50)")
    args = parser.parse_args()

    files = find_parquets(args.dir)
    if not files:
        print(f"[提示] {args.dir} 中没有 parquet 文件,可先用 build_patent_parquet.py 生成")
        return

    if args.patent:
        wanted = [p.replace(".parquet", "").strip()
                  for p in args.patent.split(",") if p.strip()]
        selected = [f for f in files if os.path.splitext(f)[0] in wanted]
        for missing in sorted(set(wanted) - {os.path.splitext(f)[0] for f in selected}):
            print(f"[警告] 目录中未找到专利 {missing} 的 parquet 文件")

        for name in selected:
            path = os.path.join(args.dir, name)
            row = read_row(path)
            print(f"\n===== {row['patent_no']} ({human_size(os.path.getsize(path))}) =====")
            if args.view in ("all", "summary"):
                print_summary(row)
            if args.view == "schema":
                print_schema(path)
            if args.view in ("all", "docs"):
                print_docs(row)
            if args.view in ("all", "conversations"):
                print_conversations(row, args.max_turns)
            if args.view in ("all", "images"):
                print_images(row)
        return

    print_list(args.dir, files)


if __name__ == "__main__":
    main()
