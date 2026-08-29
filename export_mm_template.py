"""把按专利归并的 parquet 导出为 MNBVC 通用多模态格式 (mm_template_mnbvc BLOCK_SCHEMA)。

默认读取当前目录下的专利 parquet,写出到 mm_template_parquet/,并打印对照报告。

用法:
  python export_mm_template.py
  python export_mm_template.py --src ./patent_parquet --out-dir ./mm_template_parquet
  python export_mm_template.py --compare-only
"""

import os
import re
import json
import hashlib
import argparse
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------------------------------------------------------
# 与 mm_template_mnbvc 的 BLOCK_SCHEMA 保持一致
# https://github.com/MIracleyin/mm_template_mnbvc
# ---------------------------------------------------------------------------

BLOCK_TYPE_TEXT = "text"
BLOCK_TYPE_IMAGE_TEXT_PAIR = "image-text-pair"

MEDIA_TYPE = pa.struct([("bytes", pa.large_binary()), ("path", pa.string())])

BLOCK_SCHEMA = pa.schema(
    [
        ("实体ID", pa.string()),
        ("md5", pa.string()),
        ("块ID", pa.int32()),
        ("块类型", pa.string()),
        ("扩展字段", pa.string()),
        ("时间", pa.string()),
        ("页ID", pa.int32()),
        ("文本", pa.large_string()),
        ("图片", MEDIA_TYPE),
        ("视频", MEDIA_TYPE),
        ("音频", MEDIA_TYPE),
        ("OCR文本", pa.large_string()),
        ("STT文本", pa.large_string()),
    ]
)

IMG_SRC_RE = re.compile(r'<img src="([^"]+)"')
FILE_MARKER_RE = re.compile(r"^【文件 (\d+)/(\d+)】")
PAGE_CONTINUE_RE = re.compile(r"^这是第\d+页")

FILLERS = {
    "好的,我将按时间顺序逐份分析这些文件。",
    "好的,我来分析这份文件。",
    "好的,我已知晓该内容。",
    "请继续。",
}


def content_md5(*parts):
    digest = hashlib.md5()
    for part in parts:
        if part is None or part == "":
            continue
        digest.update(part if isinstance(part, bytes) else part.encode("utf-8"))
    return digest.hexdigest()


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024


def is_patent_parquet(path):
    names = pq.read_schema(path).names
    return "patent_no" in names and "image_bytes" in names


def find_patent_parquets(src):
    if os.path.isfile(src):
        return [src] if is_patent_parquet(src) else []
    files = []
    for name in sorted(os.listdir(src)):
        if not name.endswith(".parquet"):
            continue
        path = os.path.join(src, name)
        if is_patent_parquet(path):
            files.append(path)
    return files


def read_patent_row(path):
    table = pq.read_table(path)
    if table.num_rows != 1:
        raise ValueError(f"{path} 行数不为 1")
    return table.to_pylist()[0]


def _should_stop(turn):
    value = turn["value"]
    if IMG_SRC_RE.search(value):
        return True
    if FILE_MARKER_RE.match(value):
        return True
    if value.startswith("请总结该专利"):
        return True
    return False


def _is_sft_noise(convs, index):
    turn = convs[index]
    if turn["from"] != "gpt":
        return True
    if turn["value"] in FILLERS:
        return True
    if PAGE_CONTINUE_RE.match(turn["value"]):
        return True
    prev = convs[index - 1] if index else None
    if prev and prev["from"] == "human" and prev["value"].endswith("是什么？"):
        return True
    return False


def collect_gpt_text(convs, start):
    """从 start 起收集有信息量的 gpt 文本,遇到下一张图 / 下一份文件 / 专利总结停止。"""
    parts = []
    for i in range(start, len(convs)):
        turn = convs[i]
        if i != start and _should_stop(turn):
            break
        if _is_sft_noise(convs, i):
            continue
        parts.append(turn["value"])
    return "\n\n".join(parts).strip() or None


def text_for_image(convs, image_path):
    for i, turn in enumerate(convs):
        srcs = IMG_SRC_RE.findall(turn["value"])
        if image_path in srcs:
            return collect_gpt_text(convs, i + 1)
    return None


def text_for_doc(convs, doc_index, n_docs):
    marker = f"【文件 {doc_index}/{n_docs}】"
    for i, turn in enumerate(convs):
        if turn["value"].startswith(marker):
            return collect_gpt_text(convs, i + 1)
    return None


def empty_media():
    return None


def pack_image(blob, path):
    return {"bytes": blob, "path": path}


def patent_row_to_blocks(row):
    """一件专利 -> mm_template 块列表。一页一块 image-text-pair;无图但有 XML 的文件另出 text 块。"""
    convs = row["conversations"] or []
    docs = row["docs"] or []
    images = row["images"] or []
    image_bytes = row["image_bytes"] or []
    n_docs = len(docs)

    path_to_doc = {}
    for doc in docs:
        for path in doc.get("image_files") or []:
            path_to_doc[path] = doc

    blocks = []
    block_id = 0

    for page_id, (meta, blob) in enumerate(zip(images, image_bytes)):
        doc = path_to_doc.get(meta["path"], {})
        if "xml_text" in doc:
            # 从 zip 直出时,XML 文本挂在文档上,只放到该文档首页
            text = doc["xml_text"] if meta.get("page_no") == 1 else None
        else:
            text = text_for_image(convs, meta["path"])
        ext = {
            "patent_no": row["patent_no"],
            "application_no": row["application_no"],
            "publication_no": row["publication_no"],
            "zip_name": doc.get("zip_name"),
            "seq": doc.get("seq"),
            "kind": doc.get("kind"),
            "form_type": doc.get("form_type"),
            "form_version": doc.get("form_version"),
            "lang": doc.get("lang"),
            "has_xml": doc.get("has_xml"),
            "doc_page_no": meta["page_no"],
            "n_docs": row["n_docs"],
            "n_pages": row["n_pages"],
            "width": meta["width"],
            "height": meta["height"],
            "sha256": meta["sha256"],
            "source_tiff": meta["source_tiff"],
        }
        blocks.append({
            "实体ID": row["patent_no"],
            "md5": content_md5(blob, text),
            "块ID": block_id,
            "块类型": BLOCK_TYPE_IMAGE_TEXT_PAIR,
            "扩展字段": json.dumps(ext, ensure_ascii=False),
            "时间": doc.get("pub_date") or "",
            "页ID": page_id,
            "文本": text,
            "图片": pack_image(blob, meta["path"]),
            "视频": empty_media(),
            "音频": empty_media(),
            "OCR文本": None,
            "STT文本": None,
        })
        block_id += 1

    for doc_index, doc in enumerate(docs, start=1):
        if doc.get("image_files"):
            continue
        if not doc.get("has_xml"):
            continue
        text = doc["xml_text"] if "xml_text" in doc else text_for_doc(convs, doc_index, n_docs)
        ext = {
            "patent_no": row["patent_no"],
            "application_no": row["application_no"],
            "publication_no": row["publication_no"],
            "zip_name": doc.get("zip_name"),
            "seq": doc.get("seq"),
            "kind": doc.get("kind"),
            "form_type": doc.get("form_type"),
            "form_version": doc.get("form_version"),
            "lang": doc.get("lang"),
            "has_xml": True,
            "n_docs": row["n_docs"],
            "n_pages": row["n_pages"],
        }
        blocks.append({
            "实体ID": row["patent_no"],
            "md5": content_md5(text),
            "块ID": block_id,
            "块类型": BLOCK_TYPE_TEXT,
            "扩展字段": json.dumps(ext, ensure_ascii=False),
            "时间": doc.get("pub_date") or "",
            "页ID": None,
            "文本": text,
            "图片": empty_media(),
            "视频": empty_media(),
            "音频": empty_media(),
            "OCR文本": None,
            "STT文本": None,
        })
        block_id += 1

    return blocks


def write_blocks(blocks, out_path):
    table = pa.Table.from_pylist(blocks, schema=BLOCK_SCHEMA)
    pq.write_table(table, out_path, compression="zstd")
    return table


def write_mm_parquet(row, out_path):
    """把一件专利写成 mm_template 一块一行的 parquet,返回块列表。

    row 与 build_patent_parquet.process_patent 的返回值相同:
    有 xml_text 时直接用 XML 正文;否则从 conversations 回填(用于转换已有 ShareGPT parquet)。
    """
    blocks = patent_row_to_blocks(row)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    write_blocks(blocks, out_path)
    return blocks


def export_one(src_path, out_dir):
    row = read_patent_row(src_path)
    blocks = patent_row_to_blocks(row)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{row['patent_no']}.parquet")
    table = write_blocks(blocks, out_path)
    return row, table, out_path, blocks


# ---------------------------------------------------------------------------
# 对照
# ---------------------------------------------------------------------------

def gpt_content_chars(convs):
    total = 0
    kept = 0
    for i, turn in enumerate(convs or []):
        if turn["from"] != "gpt":
            continue
        total += len(turn["value"])
        if not _is_sft_noise(convs, i):
            kept += len(turn["value"])
    return total, kept


def compare_one(src_path, out_path):
    old = read_patent_row(src_path)
    new_table = pq.read_table(out_path)
    new_rows = new_table.to_pylist()

    report = {
        "patent_no": old["patent_no"],
        "old_path": src_path,
        "new_path": out_path,
        "old_size": os.path.getsize(src_path),
        "new_size": os.path.getsize(out_path),
        "old_rows": 1,
        "new_rows": new_table.num_rows,
        "old_schema": pq.read_schema(src_path).names,
        "new_schema": list(new_table.schema.names),
        "schema_match_template": new_table.schema.equals(BLOCK_SCHEMA),
        "problems": [],
        "notes": [],
    }

    image_blocks = [r for r in new_rows if r["块类型"] == BLOCK_TYPE_IMAGE_TEXT_PAIR]
    text_blocks = [r for r in new_rows if r["块类型"] == BLOCK_TYPE_TEXT]

    report["n_image_blocks"] = len(image_blocks)
    report["n_text_blocks"] = len(text_blocks)
    report["old_n_images"] = old["n_images"]
    report["old_n_docs"] = old["n_docs"]
    report["old_n_pages"] = old["n_pages"]
    report["old_n_turns"] = len(old["conversations"] or [])

    if len(image_blocks) != old["n_images"]:
        report["problems"].append(
            f"图像块数 {len(image_blocks)} != 原 n_images {old['n_images']}"
        )

    # 按 path 对齐图像字节
    old_by_path = {
        meta["path"]: (meta, blob)
        for meta, blob in zip(old["images"], old["image_bytes"])
    }
    matched = 0
    byte_mismatch = 0
    sha_mismatch = 0
    missing = []
    for block in image_blocks:
        media = block["图片"] or {}
        path = media.get("path")
        blob = media.get("bytes")
        if path not in old_by_path:
            missing.append(path)
            continue
        meta, old_blob = old_by_path[path]
        if blob == old_blob:
            matched += 1
        else:
            byte_mismatch += 1
        ext = json.loads(block["扩展字段"])
        if ext.get("sha256") != meta["sha256"]:
            sha_mismatch += 1

    extra = set(old_by_path) - { (b["图片"] or {}).get("path") for b in image_blocks }
    report["images_matched"] = matched
    report["images_byte_mismatch"] = byte_mismatch
    report["images_sha_mismatch"] = sha_mismatch
    report["images_missing_in_new"] = sorted(extra)
    report["images_missing_in_old"] = missing

    if extra:
        report["problems"].append(f"原图未导出: {len(extra)}")
    if missing:
        report["problems"].append(f"新图在原文件中不存在: {len(missing)}")
    if byte_mismatch:
        report["problems"].append(f"图像字节不一致: {byte_mismatch}")

    # 实体 / 时间 / 页ID
    entity_ids = {r["实体ID"] for r in new_rows}
    if entity_ids != {old["patent_no"]}:
        report["problems"].append(f"实体ID={entity_ids}, 预期 {{{old['patent_no']}}}")

    page_ids = [r["页ID"] for r in image_blocks]
    if page_ids != list(range(len(image_blocks))):
        report["problems"].append("页ID 不是从 0 起的连续序号")

    block_ids = [r["块ID"] for r in new_rows]
    if block_ids != list(range(len(new_rows))):
        report["problems"].append("块ID 不是从 0 起的连续序号")

    xml_only_docs = [
        d for d in old["docs"]
        if d.get("has_xml") and not d.get("image_files")
    ]
    report["xml_only_docs"] = len(xml_only_docs)
    if len(text_blocks) != len(xml_only_docs):
        report["notes"].append(
            f"无图 XML 文件 {len(xml_only_docs)} 份, text 块 {len(text_blocks)} 个"
        )

    # 文本保留
    gpt_total, gpt_kept = gpt_content_chars(old["conversations"])
    new_text_chars = sum(len(r["文本"] or "") for r in new_rows)
    nonempty_text = sum(1 for r in new_rows if r["文本"])
    report["old_gpt_chars"] = gpt_total
    report["old_gpt_content_chars"] = gpt_kept
    report["new_text_chars"] = new_text_chars
    report["new_nonempty_text_blocks"] = nonempty_text

    # 对话轮次不会进入新格式
    report["notes"].append(
        f"原 ShareGPT {report['old_n_turns']} 轮未作为列保留,有信息量的 gpt 文本约 "
        f"{gpt_kept} 字,导出到 文本 列 {new_text_chars} 字"
    )

    # 列覆盖
    report["lost_columns"] = [
        "conversations (ShareGPT 对话列表)",
        "docs (嵌套文件列表,已打散进扩展字段)",
        "images / image_bytes (改为每行一张 struct<bytes,path>)",
        "n_docs / n_pages / n_images (写入每块扩展字段)",
    ]
    report["gained_columns"] = list(BLOCK_SCHEMA.names)

    return report


def print_report(report):
    print(f"\n===== {report['patent_no']} =====")
    print(f"  原文件: {report['old_path']}  ({human_size(report['old_size'])}, {report['old_rows']} 行)")
    print(f"  新文件: {report['new_path']}  ({human_size(report['new_size'])}, {report['new_rows']} 行)")
    print(f"  schema 对齐 mm_template: {'是' if report['schema_match_template'] else '否'}")
    print(f"  原: {report['old_n_docs']} 份文件, {report['old_n_pages']} 页, "
          f"{report['old_n_images']} 图, {report['old_n_turns']} 轮对话")
    print(f"  新: {report['n_image_blocks']} 个 image-text-pair, "
          f"{report['n_text_blocks']} 个 text"
          f"（无图 XML {report['xml_only_docs']}）")
    print(f"  图像字节一致: {report['images_matched']}/{report['old_n_images']}"
          f"（字节不一致 {report['images_byte_mismatch']}, sha 不一致 {report['images_sha_mismatch']}）")
    print(f"  文本: 原 gpt {report['old_gpt_chars']} 字 / 去噪声 {report['old_gpt_content_chars']} 字"
          f" -> 新 文本列 {report['new_text_chars']} 字"
          f"（{report['new_nonempty_text_blocks']} 块非空）")
    for note in report["notes"]:
        print(f"  [说明] {note}")
    if report["problems"]:
        for p in report["problems"]:
            print(f"  [问题] {p}")
    else:
        print("  [对照] 图像与块编号检查通过")


def print_schema_diff(old_path, new_path):
    old_schema = pq.read_schema(old_path)
    new_schema = pq.read_schema(new_path)
    print("\n【schema 对照】")
    print("  原专利 parquet:")
    for i, field in enumerate(old_schema, 1):
        print(f"    {i:>2}. {field.name:<16} {field.type}")
    print("  mm_template parquet:")
    for i, field in enumerate(new_schema, 1):
        print(f"    {i:>2}. {field.name:<16} {field.type}")


def try_concat(out_dir):
    paths = [
        os.path.join(out_dir, name)
        for name in sorted(os.listdir(out_dir))
        if name.endswith(".parquet")
    ]
    if len(paths) < 2:
        return
    tables = [pq.read_table(p) for p in paths]
    try:
        merged = pa.concat_tables(tables)
        print(f"\n【分片可拼接】{len(tables)} 个专利 parquet concat -> {merged.num_rows} 行, schema 一致")
    except Exception as e:
        print(f"\n【分片可拼接】失败: {e}")


def sample_blocks(out_path, n=3):
    rows = pq.read_table(out_path).to_pylist()
    print(f"\n【新格式样例】{os.path.basename(out_path)} 前 {min(n, len(rows))} 块")
    for row in rows[:n]:
        media = row["图片"] or {}
        text = (row["文本"] or "").replace("\n", " | ")
        print(f"  块ID={row['块ID']} 类型={row['块类型']} 页ID={row['页ID']} "
              f"时间={row['时间'] or '-'} path={media.get('path')} "
              f"bytes={len(media['bytes']) if media.get('bytes') else 0} "
              f"md5={row['md5'][:12]}")
        print(f"    文本[{len(row['文本'] or '')}]: {text[:180]}")
        ext = json.loads(row["扩展字段"])
        keys = ("zip_name", "form_type", "has_xml", "doc_page_no", "sha256")
        print("    扩展:", {k: ext.get(k) for k in keys})


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="导出专利 parquet 为 mm_template 通用多模态格式,并与原文件对照。")
    parser.add_argument("--src", default=".",
                        help="专利 parquet 文件或目录 (默认当前目录)")
    parser.add_argument("--out-dir", default="./mm_template_parquet",
                        help="导出目录 (默认 ./mm_template_parquet)")
    parser.add_argument("--compare-only", action="store_true",
                        help="不重新导出,只对照 --out-dir 里已有文件")
    parser.add_argument("--sample", type=int, default=2,
                        help="每个新文件打印的样例块数 (默认 2)")
    args = parser.parse_args()

    src_files = find_patent_parquets(args.src)
    if not src_files:
        print(f"[错误] {args.src} 中没有专利 parquet (需含 patent_no / image_bytes 列)")
        return 1

    reports = []
    first_old = src_files[0]
    first_new = None

    for src_path in src_files:
        patent = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(args.out_dir, f"{patent}.parquet")
        if args.compare_only:
            if not os.path.exists(out_path):
                print(f"[跳过] 未找到导出文件 {out_path}")
                continue
            row = read_patent_row(src_path)
            blocks = None
        else:
            row, table, out_path, blocks = export_one(src_path, args.out_dir)
            n_img = sum(1 for b in blocks if b["块类型"] == BLOCK_TYPE_IMAGE_TEXT_PAIR)
            n_txt = sum(1 for b in blocks if b["块类型"] == BLOCK_TYPE_TEXT)
            print(f"[导出] {row['patent_no']}: {len(blocks)} 块 "
                  f"({n_img} 图, {n_txt} 文本) -> {out_path} ({human_size(os.path.getsize(out_path))})")
        if first_new is None:
            first_new = out_path
        reports.append(compare_one(src_path, out_path))

    if first_new:
        print_schema_diff(first_old, first_new)
        if args.sample:
            sample_blocks(first_new, args.sample)

    print("\n" + "=" * 64)
    print("对照汇总")
    print("=" * 64)
    for report in reports:
        print_report(report)

    if not args.compare_only:
        try_concat(args.out_dir)

    failed = sum(1 for r in reports if r["problems"])
    print(f"\n完成: 导出/对照 {len(reports)} 个专利, {failed} 个有问题。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
