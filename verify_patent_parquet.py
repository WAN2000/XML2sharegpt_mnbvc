import os
import re
import sys
import json
import argparse
from io import BytesIO
from collections import defaultdict

import pyarrow.parquet as pq

# 与 build_patent_parquet.py 保持一致的 zip 文件名解析
ZIP_PATTERN = re.compile(r'^(WO\d{4})(\d+)(?:_(\d+))?\.zip$', re.IGNORECASE)

EXPECTED_COLUMNS = [
    "patent_no", "application_no", "publication_no",
    "n_docs", "n_pages", "n_images",
    "docs", "conversations", "images", "image_bytes",
]


def find_parquets(parquet_dir):
    return sorted(f for f in os.listdir(parquet_dir) if f.endswith('.parquet'))


def read_row(path):
    table = pq.read_table(path)
    if table.num_rows != 1:
        raise ValueError(f"{path} 行数不为 1")
    return table.to_pylist()[0]


def select_files(parquet_dir, patents):
    """按专利号白名单挑选 parquet 文件;patents 为 None 时返回全部。"""
    files = []
    for name in find_parquets(parquet_dir):
        stem = os.path.splitext(name)[0]
        if patents is None or stem in patents:
            files.append(os.path.join(parquet_dir, name))
    if patents:
        found = {os.path.splitext(os.path.basename(f))[0] for f in files}
        for missing in sorted(set(patents) - found):
            print(f"[警告] 未找到专利 {missing} 的 parquet 文件")
    return files


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024


def date_range(row):
    dates = [d["pub_date"] for d in row["docs"] if d["pub_date"]]
    if not dates:
        return "-"
    return f"{min(dates)}~{max(dates)}"


# ---------------------------------------------------------------------------
# 查看
# ---------------------------------------------------------------------------

def print_list(parquet_dir):
    """列出所有 parquet 的概要。"""
    files = find_parquets(parquet_dir)
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
    print("\n【页面图像】")
    print(f"{'#':>4} {'文件名':<34} {'页':>3} {'宽x高':<12} {'大小':>9} {'sha256前12位':<14} 来源TIFF")
    for i, (meta, b) in enumerate(zip(row["images"], row["image_bytes"]), 1):
        wh = f"{meta['width']}x{meta['height']}"
        print(f"{i:>4} {meta['path']:<34} {meta['page_no']:>3} "
              f"{wh:<12} {human_size(len(b)):>9} "
              f"{meta['sha256'][:12]:<14} {meta['source_tiff']}")
    if row["n_images"] != len(row["images"]):
        print(f"  [警告] n_images={row['n_images']} 与 images 列表长度 {len(row['images'])} 不一致")


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def do_export(row, export_root):
    """把专利的对话文本与 PNG 图像导出到目录,便于直接翻阅。"""
    base = os.path.join(export_root, row["patent_no"])
    image_dir = os.path.join(base, "images")
    os.makedirs(image_dir, exist_ok=True)

    data = {
        "patent_no": row["patent_no"],
        "application_no": row["application_no"],
        "publication_no": row["publication_no"],
        "docs": row["docs"],
        "conversations": row["conversations"],
        "images": row["images"],
    }
    json_path = os.path.join(base, "conversation.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    txt_lines = []
    for t in row["conversations"]:
        txt_lines.append(f"【{t['from']}】")
        txt_lines.append(t["value"])
        txt_lines.append("")
    txt_path = os.path.join(base, "conversation.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines))

    for meta, b in zip(row["images"], row["image_bytes"]):
        with open(os.path.join(image_dir, meta["path"]), "wb") as f:
            f.write(b)

    print(f"已导出到 {base}/")
    print(f"  conversation.json (完整数据) / conversation.txt (可读文本) "
          f"/ images/ ({len(row['images'])} 张 PNG)")


# ---------------------------------------------------------------------------
# 校验(原 verify_patent_parquet.py 的逻辑)
# ---------------------------------------------------------------------------

def verify_all(parquet_dir, zip_dir, corpus_json, patents):
    # 源目录中每个专利应有的 zip 数
    expected_zips = defaultdict(list)
    for name in os.listdir(zip_dir):
        m = ZIP_PATTERN.match(name)
        if m:
            expected_zips[f"{m.group(1)}{m.group(2)}"].append(name)

    # 旧管线 corpus_records.json 的页数对照表
    corpus_pages = {}
    if os.path.exists(corpus_json):
        with open(corpus_json, encoding='utf-8') as f:
            corpus = json.load(f)
        for r in corpus.get("records", []):
            corpus_pages[r["id"]] = r.get("page_count", 0)

    files = select_files(parquet_dir, patents)
    if not files:
        print(f"[失败] {parquet_dir} 中没有 parquet 文件")
        return 1

    problems = []
    print(f"检查 {len(files)} 个 parquet 文件...\n")

    for path in files:
        name = os.path.basename(path)
        patent_no = os.path.splitext(name)[0]
        print(f"[{name}]")

        table = pq.read_table(path)
        row = table.to_pylist()[0]

        def check(ok, msg):
            if not ok:
                problems.append((name, msg))
            print(f"  {'通过' if ok else '失败'}: {msg}")

        check(table.num_rows == 1, f"行数 = 1 (实际 {table.num_rows})")
        check(list(table.column_names) == EXPECTED_COLUMNS, "列名与预期 schema 一致")
        check(row["patent_no"] == patent_no, f"patent_no = {row['patent_no']}")

        n_zips = len(expected_zips.get(patent_no, []))
        check(n_zips > 0 and row["n_docs"] == n_zips,
              f"n_docs = {row['n_docs']},源目录该专利 zip 数 = {n_zips}")
        check(len(row["docs"]) == row["n_docs"], f"docs 列表长度 = {len(row['docs'])}")
        check(sum(d["page_count"] for d in row["docs"]) == row["n_pages"],
              f"各 doc page_count 之和 = n_pages ({row['n_pages']})")
        check(len(row["images"]) == len(row["image_bytes"]) == row["n_images"],
              f"images({len(row['images'])}) == image_bytes({len(row['image_bytes'])}) "
              f"== n_images({row['n_images']})")

        dates = [d["pub_date"] for d in row["docs"] if d["pub_date"]]
        check(dates == sorted(dates), "docs 按 pub_date 升序排列")

        convs = row["conversations"]
        check(len(convs) >= 2 * row["n_docs"] + 2, f"对话条数充足 ({len(convs)})")
        alternating = all(
            (i % 2 == 0 and c["from"] == "human") or (i % 2 == 1 and c["from"] == "gpt")
            for i, c in enumerate(convs)
        )
        check(alternating, "对话 human/gpt 交替")
        markers = sum(1 for c in convs if c["value"].startswith("【文件 "))
        check(markers == row["n_docs"], f"文件引导语数量 = {markers} (预期 {row['n_docs']})")

        if row["image_bytes"]:
            try:
                from PIL import Image
                decoded = 0
                for b in (row["image_bytes"][0], row["image_bytes"][-1]):
                    try:
                        img = Image.open(BytesIO(b))
                        img.load()
                        decoded += 1
                    except Exception as e:
                        print(f"     [PNG 解码失败] {e}")
                check(decoded == min(2, len(row["image_bytes"])), "首末 PNG 字节可解码")
            except ImportError:
                print("  [跳过] 未安装 Pillow,跳过 PNG 解码检查")

        if corpus_pages:
            mismatches = []
            for d in row["docs"]:
                zid = d["zip_name"].replace(".zip", "")
                if zid in corpus_pages and corpus_pages[zid] != d["page_count"]:
                    mismatches.append(f"{zid}: parquet={d['page_count']} vs corpus={corpus_pages[zid]}")
            check(not mismatches, f"与 corpus_records.json 页数一致 ({' ;'.join(mismatches[:3])}...)")

        print(f"  [信息] {row['n_docs']} 份文件、{row['n_pages']} 页、{row['n_images']} 张图、"
              f"{len(convs)} 条对话, 申请号={row['application_no']}, 公开号={row['publication_no']}")

    print("\n" + "=" * 60)
    if problems:
        print(f"共 {len(problems)} 项检查失败:")
        for name, msg in problems:
            print(f"  {name}: {msg}")
        return 1
    print(f"全部 {len(files)} 个 parquet 检查通过。")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="查看 / 导出 / 校验按专利归并的 parquet 文件。\n"
                    "不带参数时列出所有 parquet 概要;--patent 查看单个专利详情。")
    parser.add_argument("--parquet-dir", default="./patent_parquet")
    parser.add_argument("--patent", default=None,
                        help="指定专利号(逗号分隔多个),用于查看/导出/校验指定文件")
    parser.add_argument("--view", default="all",
                        choices=["all", "summary", "docs", "conversations", "images"],
                        help="查看内容:all=全部,summary=概要,docs=各时期文件表,"
                             "conversations=对话,images=图像表 (默认 all)")
    parser.add_argument("--max-turns", type=int, default=50,
                        help="conversations 视图最多显示的对话轮数 (0=全部,默认 50)")
    parser.add_argument("--export-dir", default=None,
                        help="把指定专利的对话文本与 PNG 图像导出到该目录")
    parser.add_argument("--verify", action="store_true",
                        help="运行完整性校验(与源 zip 数、页数、对话交替等交叉核对)")
    parser.add_argument("--zip-dir", default=r"H:\BaiduNetdiskDownload\random_1000_patents",
                        help="专利 zip 源目录(仅 --verify 使用)")
    parser.add_argument("--corpus-json", default="./corpus_records.json",
                        help="旧管线输出,用于交叉核对页数(仅 --verify 使用)")
    args = parser.parse_args()

    patents = None
    if args.patent:
        patents = [p.replace(".parquet", "").strip()
                   for p in args.patent.split(",") if p.strip()]

    if args.verify:
        sys.exit(verify_all(args.parquet_dir, args.zip_dir, args.corpus_json, patents))

    if args.export_dir:
        for path in select_files(args.parquet_dir, patents):
            row = read_row(path)
            print(f"导出 {row['patent_no']} ...")
            do_export(row, args.export_dir)
        return

    if patents:
        for path in select_files(args.parquet_dir, patents):
            row = read_row(path)
            print(f"\n===== {row['patent_no']} =====")
            if args.view in ("all", "summary"):
                print_summary(row)
            if args.view in ("all", "docs"):
                print_docs(row)
            if args.view in ("all", "conversations"):
                print_conversations(row, args.max_turns)
            if args.view in ("all", "images"):
                print_images(row)
        return

    print_list(args.parquet_dir)


if __name__ == "__main__":
    main()
