import os
import re
import json
import zipfile
import argparse
import xml.etree.ElementTree as ET
from collections import defaultdict

import pyarrow as pa
import pyarrow.parquet as pq

from build_corpus_records import analyze_zip_deep
from build_multimodal_sharegpt import (
    compute_sha256,
    extract_tiff_from_zip,
    convert_tiff_to_png,
    build_conversations,
    get_form_description,
)

# zip 文件名:WO2014139619.zip 或 WO2014139619_1.zip
# (下划线前为专利号,下划线后的数字为同一专利的不同时期文件序号)
ZIP_PATTERN = re.compile(r'^(WO\d{4})(\d+)(?:_(\d+))?\.zip$', re.IGNORECASE)

# parquet 中 docs 列表元素的结构
DOC_FIELDS = [
    ("seq", pa.int64()),
    ("zip_name", pa.string()),
    ("kind", pa.string()),
    ("pub_date", pa.string()),
    ("form_type", pa.string()),
    ("form_version", pa.string()),
    ("lang", pa.string()),
    ("page_count", pa.int64()),
    ("image_files", pa.list_(pa.string())),
    ("has_xml", pa.bool_()),
    ("error", pa.string()),
]

SCHEMA = pa.schema([
    ("patent_no", pa.string()),
    ("application_no", pa.string()),
    ("publication_no", pa.string()),
    ("n_docs", pa.int64()),
    ("n_pages", pa.int64()),
    ("n_images", pa.int64()),
    ("docs", pa.list_(pa.struct(DOC_FIELDS))),
    ("conversations", pa.list_(pa.struct([
        ("from", pa.string()),
        ("value", pa.string()),
    ]))),
    ("images", pa.list_(pa.struct([
        ("path", pa.string()),
        ("page_no", pa.int64()),
        ("width", pa.int64()),
        ("height", pa.int64()),
        ("sha256", pa.string()),
        ("source_tiff", pa.string()),
    ]))),
    ("image_bytes", pa.list_(pa.binary())),
])


def parse_zip_name(zip_name):
    """解析 zip 文件名,返回 {patent_no, publication_no, sequence_no},不匹配返回 None。"""
    m = ZIP_PATTERN.match(zip_name)
    if not m:
        return None
    return {
        "patent_no": f"{m.group(1)}{m.group(2)}",
        "publication_no": f"{m.group(1)}/{m.group(2)}",
        "sequence_no": int(m.group(3)) if m.group(3) else None,
    }


def extract_pub_info(zip_path):
    """从 zip 内主 XML 提取 (kind, pub_date):
    优先 publication-reference/document-id 的 kind/date 子元素,
    缺失时回退根属性 date-produced / date-sending。"""
    kind = None
    pub_date = None
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            xml_files = [f for f in z.namelist() if f.lower().endswith('.xml')
                         and not f.lower().startswith('page-count')]

            # 与 analyze_zip_deep 一致:优先非 _std 变体
            main_xml = None
            std_xml = None
            for xf in xml_files:
                if '_std' in xf.lower():
                    std_xml = xf
                else:
                    main_xml = xf
            if not main_xml and std_xml:
                main_xml = std_xml
            if not main_xml:
                return kind, pub_date

            root = ET.fromstring(z.read(main_xml))
            pub_ref = root.find(".//publication-reference/document-id")
            if pub_ref is not None:
                kind_el = pub_ref.find("kind")
                date_el = pub_ref.find("date")
                if kind_el is not None and kind_el.text:
                    kind = kind_el.text
                if date_el is not None and date_el.text:
                    pub_date = date_el.text
            if not pub_date:
                pub_date = root.attrib.get("date-produced") or root.attrib.get("date-sending")
    except Exception:
        pass
    return kind, pub_date


def build_doc(record, zip_path):
    """基于 analyze_zip_deep 的分析结果构建单个时期文档:
    补充 kind/pub_date,提取 TIFF 转 PNG 字节,生成该文档的 ShareGPT 对话段。"""
    kind, pub_date = extract_pub_info(zip_path)

    images = []
    image_bytes_list = []
    page_errors = []
    tiff_pages = record.get("tiff_pages", [])

    for idx, page in enumerate(tiff_pages, start=1):
        try:
            tiff_bytes = extract_tiff_from_zip(zip_path, page["file"])
            png_bytes = convert_tiff_to_png(tiff_bytes)
            images.append({
                "path": f"{record['id']}_{idx:06d}.png",
                "page_no": idx,
                "width": page["width"],
                "height": page["height"],
                "sha256": compute_sha256(png_bytes),
                "source_tiff": page["file"],
            })
            image_bytes_list.append(png_bytes)
        except Exception as e:
            page_errors.append(f"page {idx} ({page['file']}): {e}")

    form = record.get("form") or {}
    conversations = build_conversations(record, image_dir="")

    return {
        "seq": record.get("sequence_no"),
        "zip_name": record["zip_name"],
        "kind": kind,
        "pub_date": pub_date,
        "form_type": form.get("type"),
        "form_version": form.get("version"),
        "lang": form.get("lang"),
        "page_count": record.get("page_count", 0),
        "image_files": [img["path"] for img in images],
        "has_xml": bool(record.get("xml_info")),
        "error": "; ".join(filter(None, [record.get("error"), *page_errors])) or None,
        # 内部字段(写入 parquet 前移除)
        "application_no": record.get("application_no"),
        "images": images,
        "image_bytes": image_bytes_list,
        "conversations": conversations,
    }


def _doc_form_desc(doc):
    """生成文档的表单描述,无表单信息时返回通用描述。"""
    form_info = {
        "type": doc.get("form_type"),
        "version": doc.get("form_version"),
        "lang": doc.get("lang"),
    }
    if not any(form_info.values()):
        return "未知类型的PCT专利文档"
    return get_form_description(form_info)


def _append_alternating(conversations, turns):
    """追加对话段,若与上一条角色相同则先补一条对侧角色的应答,保证整条对话 human/gpt 严格交替。"""
    for turn in turns:
        if conversations and conversations[-1]["from"] == turn["from"]:
            if turn["from"] == "human":
                conversations.append({"from": "gpt", "value": "好的,我已知晓该内容。"})
            else:
                conversations.append({"from": "human", "value": "请继续。"})
        conversations.append(turn)


def merge_conversations(patent_no, publication_no, application_no, docs):
    """把各时期文档的对话段按时间顺序拼接为一条完整的 ShareGPT 对话。"""
    conversations = []
    n = len(docs)

    _append_alternating(conversations, [
        {
            "from": "human",
            "value": f"以下是专利 {publication_no or patent_no} 的全部PCT文件档案,共 {n} 份不同时期的文件。请逐份分析。"
        },
        {
            "from": "gpt",
            "value": "好的,我将按时间顺序逐份分析这些文件。"
        },
    ])

    for i, doc in enumerate(docs, start=1):
        parts = []
        if doc["kind"]:
            parts.append(f"公开类型 {doc['kind']}")
        if doc["pub_date"]:
            parts.append(f"日期 {doc['pub_date']}")
        parts.append(_doc_form_desc(doc))

        _append_alternating(conversations, [{
            "from": "human",
            "value": f"【文件 {i}/{n}】{', '.join(parts)}。请分析这份文件。"
        }])

        doc_turns = doc["conversations"]
        if doc_turns and doc_turns[0]["from"] == "gpt":
            # 文档段以 gpt 开头(纯 XML 文档)时直接衔接,由它回答上面的引导语
            pass
        else:
            conversations.append({"from": "gpt", "value": "好的,我来分析这份文件。"})
        _append_alternating(conversations, doc_turns)

    total_pages = sum(d["page_count"] for d in docs)
    _append_alternating(conversations, [
        {
            "from": "human",
            "value": "请总结该专利在全部文件中的整体信息。"
        },
        {
            "from": "gpt",
            "value": f"该专利 {publication_no or patent_no} 共收录 {n} 份文件、{total_pages} 页,"
                     f"记录了该专利申请从受理、检索到国际公布的完整流程信息。"
        },
    ])

    return conversations


def process_patent(patent_no, publication_no, zip_names, zip_dir):
    """处理一个专利的全部时期 zip,返回该专利的 parquet 行数据。"""
    docs = []
    errors = []

    for zip_name in zip_names:
        zip_path = os.path.join(zip_dir, zip_name)
        try:
            record = analyze_zip_deep(zip_path)
            docs.append(build_doc(record, zip_path))
        except Exception as e:
            errors.append(f"{zip_name}: {e}")
            print(f"    [错误] {zip_name}: {e}")

    # 按文档内日期排序;无 XML 日期的图片型 zip 按序号排在后面
    docs.sort(key=lambda d: (
        d["pub_date"] or "99999999",
        d["seq"] if d["seq"] is not None else 0,
        d["zip_name"],
    ))

    application_no = next((d["application_no"] for d in docs if d.get("application_no")), None)

    conversations = merge_conversations(patent_no, publication_no, application_no, docs)

    # 拆出各文档的图像元数据与字节,并移除内部字段
    images = []
    image_bytes_list = []
    for d in docs:
        images.extend(d.pop("images"))
        image_bytes_list.extend(d.pop("image_bytes"))
        d.pop("application_no", None)
        d.pop("conversations", None)

    row = {
        "patent_no": patent_no,
        "application_no": application_no,
        "publication_no": publication_no,
        "n_docs": len(docs),
        "n_pages": sum(d["page_count"] for d in docs),
        "n_images": len(images),
        "docs": docs,
        "conversations": conversations,
        "images": images,
        "image_bytes": image_bytes_list,
    }
    return row, errors


def write_patent_parquet(row, out_path):
    """把单个专利的行数据写入 parquet 文件。"""
    row["docs"] = [{k: d.get(k) for k, _ in DOC_FIELDS} for d in row["docs"]]
    table = pa.Table.from_pylist([row], schema=SCHEMA)
    pq.write_table(table, out_path, compression="zstd")


def main():
    parser = argparse.ArgumentParser(
        description="按专利归并各时期 zip 文件,每个专利生成一个内嵌页面图像的 parquet。")
    parser.add_argument("--zip-dir", default=r"H:\BaiduNetdiskDownload\random_1000_patents",
                        help="专利 zip 源目录")
    parser.add_argument("--out-dir", default="./patent_parquet",
                        help="parquet 输出目录")
    parser.add_argument("--patents", default=None,
                        help="逗号分隔的专利号白名单,如 WO2014139619,WO2017021797;不传则处理全部")
    parser.add_argument("--limit", type=int, default=None,
                        help="最多处理的专利数")
    args = parser.parse_args()

    # 扫描源目录并按专利号分组
    groups = defaultdict(list)
    zip_count = 0
    for name in sorted(os.listdir(args.zip_dir)):
        if not name.lower().endswith('.zip'):
            continue
        parsed = parse_zip_name(name)
        if not parsed:
            continue
        groups[parsed["patent_no"]].append(name)
        zip_count += 1

    print(f"源目录中发现 {len(groups)} 个专利、{zip_count} 个 zip 文件。")

    if args.patents:
        wanted = [p.strip() for p in args.patents.split(',') if p.strip()]
        missing = [p for p in wanted if p not in groups]
        if missing:
            print(f"[警告] 源目录中未找到这些专利: {missing}")
        groups = {p: groups[p] for p in wanted if p in groups}
    if args.limit:
        groups = dict(sorted(groups.items())[:args.limit])

    os.makedirs(args.out_dir, exist_ok=True)

    stats = {
        "total_patents": len(groups),
        "total_docs": 0,
        "total_pages": 0,
        "total_images": 0,
        "errors": [],
    }
    generated_files = []

    for i, (patent_no, zip_names) in enumerate(sorted(groups.items()), start=1):
        print(f"\n[{i}/{len(groups)}] 处理专利 {patent_no} ({len(zip_names)} 个 zip)...")
        publication_no = parse_zip_name(zip_names[0])["publication_no"]

        row, errors = process_patent(patent_no, publication_no, zip_names, args.zip_dir)

        out_path = os.path.join(args.out_dir, f"{patent_no}.parquet")
        write_patent_parquet(row, out_path)
        generated_files.append(os.path.basename(out_path))

        stats["total_docs"] += row["n_docs"]
        stats["total_pages"] += row["n_pages"]
        stats["total_images"] += row["n_images"]
        for err in errors:
            stats["errors"].append({"patent_no": patent_no, "error": err})

        print(f"  [完成] {row['n_docs']} 份文件、{row['n_pages']} 页、{row['n_images']} 张图、"
              f"{len(row['conversations'])} 条对话 -> {out_path}")
        if errors:
            print(f"  [警告] {len(errors)} 个 zip 处理失败")

    index_path = os.path.join(args.out_dir, "index.json")
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump({"statistics": stats, "generated_files": generated_files},
                  f, ensure_ascii=False, indent=2)

    print("\n" + "-" * 60)
    print(f"处理完成: {stats['total_patents']} 个专利 -> {args.out_dir}")
    print(f"  文件数: {stats['total_docs']}, 页数: {stats['total_pages']}, 图像数: {stats['total_images']}")
    if stats["errors"]:
        print(f"  错误数: {len(stats['errors'])}")
        for err in stats["errors"][:20]:
            print(f"    {err['patent_no']}: {err['error']}")
    print(f"索引文件: {index_path}")


if __name__ == "__main__":
    main()
