import os
import json
import zipfile
import xml.etree.ElementTree as ET
import re
from io import BytesIO
from PIL import Image
from collections import defaultdict


def extract_tiff_footer_data(tiff_bytes: bytes) -> dict:
    """
    从 TIFF 底部区域提取页脚信息。
    PCT 文档页脚通常在最底部 120-200 像素高度处，
    格式为: 页码/文件ID (如 1/QRJAQ5PRPYKAM0)
    """
    img = Image.open(BytesIO(tiff_bytes))
    width, height = img.size

    # 裁切底部 200 像素 (页脚区域)
    footer_box = (0, height - 200, width, height)
    footer = img.crop(footer_box)

    return {
        "width": width,
        "height": height,
        "mode": img.mode,
        "footer_region": f"({0}, {height-200}, {width}, {height})"
    }


def parse_xml_root_attrs(xml_bytes: bytes) -> dict:
    """解析 XML 根标签属性。"""
    root = ET.fromstring(xml_bytes)
    return {
        "tag": root.tag,
        "file": root.attrib.get("file"),
        "do": root.attrib.get("do"),
        "lang": root.attrib.get("lang"),
        "date_produced": root.attrib.get("date-produced"),
        "date_sending": root.attrib.get("date-sending"),
        "status": root.attrib.get("status"),
        "produced_by": root.attrib.get("produced-by"),
        "dtd_version": root.attrib.get("dtd-version"),
        "form_id": root.attrib.get("form-id"),
        "form_version": root.attrib.get("form-version"),
    }


def xml_to_nested_dict(element: ET.Element) -> dict:
    """将 XML 元素树递归转换为嵌套字典，保留完整结构。"""
    result = {"tag": element.tag}

    # 属性
    if element.attrib:
        result["attrs"] = dict(element.attrib)

    # 文本内容（去除首尾空白）
    text = element.text.strip() if element.text else None
    if text:
        result["text"] = text

    # 子元素
    children = list(element)
    if children:
        # 按标签名分组，处理重复子标签
        child_groups = {}
        for child in children:
            tag = child.tag
            child_dict = xml_to_nested_dict(child)
            if tag not in child_groups:
                child_groups[tag] = []
            child_groups[tag].append(child_dict)

        # 单个子标签直接展开，多个子标签保留列表
        child_list = []
        for tag, items in child_groups.items():
            if len(items) == 1:
                child_list.append(items[0])
            else:
                child_list.append({"tag": tag, "items": items})

        result["children"] = child_list

    return result


def parse_do_attr(do_attr: str) -> dict:
    """解析 do 属性，如 IB304_201207_en。"""
    if not do_attr:
        return None
    parts = do_attr.split("_")
    if len(parts) >= 3:
        return {
            "form_type": parts[0].lower(),
            "version": parts[1],
            "lang": parts[2],
            "raw": do_attr
        }
    return {"raw": do_attr}


def analyze_zip_deep(zip_path: str) -> dict:
    """深度分析一个 zip 包：XML + TIFF 关联。"""
    zip_name = os.path.basename(zip_path)
    record_id = os.path.splitext(zip_name)[0]

    # 从文件名解析公开号和序号
    m = re.match(r'^(WO\d{4})(\d+)_(\d+)$', record_id)
    publication_no = None
    sequence_no = None
    if m:
        publication_no = f"{m.group(1)}/{m.group(2)}"
        sequence_no = int(m.group(3))

    result = {
        "id": record_id,
        "publication_no": publication_no,
        "sequence_no": sequence_no,
        "zip_name": zip_name,
        "xml_info": None,
        "tiff_pages": [],
        "application_no": None,
        "file_reference_id": None,
        "do_parsed": None,
        "form": None,
        "page_count": 0,
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            xml_files = [f for f in z.namelist() if f.lower().endswith('.xml')
                         and not f.lower().startswith('page-count')]
            tiff_files = sorted([f for f in z.namelist()
                                 if f.lower().endswith(('.tif', '.tiff'))])

            # 解析主 XML (排除 _std 变体，优先用非 std 版)
            main_xml = None
            std_xml = None
            for xf in xml_files:
                if '_std' in xf.lower():
                    std_xml = xf
                else:
                    main_xml = xf
            if not main_xml and std_xml:
                main_xml = std_xml

            if main_xml:
                xml_bytes = z.read(main_xml)
                attrs = parse_xml_root_attrs(xml_bytes)
                result["xml_info"] = {
                    "file_name": main_xml,
                    **attrs
                }
                result["do_parsed"] = parse_do_attr(attrs.get("do"))

                # 提取 form 信息
                form_type = None
                form_version = None
                form_lang = attrs.get("lang")

                if result["do_parsed"]:
                    form_type = result["do_parsed"]["form_type"]
                    form_version = result["do_parsed"]["version"]
                    form_lang = result["do_parsed"]["lang"]
                elif attrs.get("form_id"):
                    # wo-form-ro / wo-form-ib
                    parts = attrs["form_id"].split("/")
                    if len(parts) >= 3:
                        form_type = parts[-2].lower() + parts[-1]
                    form_version = attrs.get("form_version")
                else:
                    form_type = attrs.get("tag")

                result["form"] = {
                    "type": form_type,
                    "version": form_version,
                    "lang": form_lang,
                    "file_id": attrs.get("file"),
                    "source": "do_attr" if result["do_parsed"] else "form_id" if attrs.get("form_id") else "tag"
                }

                # 提取通用字段
                root = ET.fromstring(xml_bytes)
                file_ref = root.find("file-reference-id")
                app_ref = root.find(".//application-reference/document-id/doc-number")
                pub_ref = root.find(".//publication-reference/document-id/doc-number")

                if file_ref is not None:
                    result["file_reference_id"] = file_ref.text
                if app_ref is not None:
                    app_no = app_ref.text
                    if app_no and app_no.isdigit():
                        app_no = f"IB{app_no}"
                    result["application_no"] = app_no
                if pub_ref is not None:
                    result["publication_no"] = pub_ref.text

            # 解析 TIFF 页
            for tf in tiff_files:
                tiff_bytes = z.read(tf)
                img = Image.open(BytesIO(tiff_bytes))
                result["tiff_pages"].append({
                    "file": tf,
                    "width": img.width,
                    "height": img.height,
                    "mode": img.mode,
                    "frames": img.n_frames,
                    "size_bytes": len(tiff_bytes)
                })

            result["page_count"] = len(result["tiff_pages"])

            # 第四层：提取 XML 完整结构 + 字段值用于交叉校验
            if main_xml:
                root = ET.fromstring(z.read(main_xml))
                result["structured"] = xml_to_nested_dict(root)

                field_values = set()
                for elem in root.iter():
                    if elem.text and elem.text.strip():
                        field_values.add(elem.text.strip()[:60])
                result["field_values"] = sorted(list(field_values))[:50]

    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    zip_dir = "./zipFiles"
    zip_files = sorted([f for f in os.listdir(zip_dir) if f.lower().endswith('.zip')])

    records = []
    for zip_name in zip_files:
        zip_path = os.path.join(zip_dir, zip_name)
        record = analyze_zip_deep(zip_path)
        records.append(record)

    # 统计
    stats = {
        "total": len(records),
        "with_xml": sum(1 for r in records if r["xml_info"]),
        "with_tiff": sum(1 for r in records if r["tiff_pages"]),
        "total_pages": sum(r["page_count"] for r in records),
        "form_types": defaultdict(int)
    }

    for r in records:
        if r["form"] and r["form"]["type"]:
            stats["form_types"][r["form"]["type"]] += 1

    # 公开号级聚合
    pub_index = defaultdict(list)
    for r in records:
        key = r.get("publication_no") or "unknown"
        pub_index[key].append(r["id"])

    # 申请号级聚合
    app_index = defaultdict(list)
    for r in records:
        key = r.get("application_no") or r.get("file_reference_id") or "unknown"
        app_index[key].append({
            "id": r["id"],
            "form": r["form"]["type"] if r["form"] else None,
            "seq": r["sequence_no"],
            "pages": r["page_count"]
        })

    output = {
        "statistics": dict(stats),
        "records": records,
        "publication_index": {k: sorted(v) for k, v in pub_index.items()},
        "application_index": dict(app_index)
    }

    with open("./corpus_records.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已生成 {len(records)} 条语料记录 -> corpus_records.json")
    print(f"\n统计:")
    print(f"  zip 总数: {stats['total']}")
    print(f"  含 XML: {stats['with_xml']}")
    print(f"  含 TIFF: {stats['with_tiff']}")
    print(f"  总页数: {stats['total_pages']}")
    print(f"\n表单类型分布:")
    for ft, cnt in sorted(stats['form_types'].items()):
        print(f"    {ft}: {cnt}")

    print(f"\n四层线索验证 (示例):")
    for r in records[:5]:
        form = r.get("form") or {}
        print(f"\n  [{r['id']}]")
        print(f"    包级: publication_no={r.get('publication_no')}, seq={r.get('sequence_no')}")
        print(f"    页级: file_id={form.get('file_id')}, pages={r['page_count']}")
        print(f"    模板级: type={form.get('type')}, version={form.get('version')}, lang={form.get('lang')}")
        vals = r.get("field_values", [])
        print(f"    字段级: {len(vals)} 个可变字段值, 示例={vals[:5]}")

    # 输出 image-only zip 列表
    image_only = [r for r in records if not r["xml_info"] and r["tiff_pages"]]
    print(f"\nimage-only zip ({len(image_only)} 个):")
    for r in image_only:
        print(f"    {r['id']}: {r['page_count']} pages")


if __name__ == "__main__":
    main()