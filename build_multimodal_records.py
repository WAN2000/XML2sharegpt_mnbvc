import os
import json
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from PIL import Image


def compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_xml_header(xml_path: str) -> Dict[str, Any]:
    """解析 XML 根标签及其关键属性。"""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # 公开号/申请号：优先从 publication-reference 取，否则 application-reference
    pub_ref = root.find('.//publication-reference/document-id/doc-number')
    app_ref = root.find('.//application-reference/document-id/doc-number')
    publication_no = pub_ref.text if pub_ref is not None else None
    application_no = app_ref.text if app_ref is not None else None

    # file-reference-id
    file_ref = root.find('file-reference-id')
    file_reference_id = file_ref.text if file_ref is not None else None

    file_attr = root.attrib.get('file', '')
    do_attr = root.attrib.get('do', '')

    return {
        "root_tag": root.tag,
        "file_attr": file_attr,
        "do_attr": do_attr,
        "lang": root.attrib.get('lang'),
        "date_produced": root.attrib.get('date-produced'),
        "publication_no": publication_no,
        "application_no": application_no,
        "file_reference_id": file_reference_id,
    }


def parse_form_info(xml_info: Dict[str, Any]) -> Dict[str, Any]:
    """从 do 属性或根标签推断模板信息。"""
    do = xml_info.get('do_attr', '')
    root_tag = xml_info.get('root_tag', '')

    if '_' in do:
        parts = do.split('_')
        if len(parts) >= 3:
            return {
                "type": parts[0].lower(),
                "version": parts[1],
                "lang": parts[2],
                "source": "do_attribute",
                "raw": do
            }
        elif len(parts) == 2:
            return {
                "type": parts[0].lower(),
                "version": parts[1],
                "lang": xml_info.get('lang'),
                "source": "do_attribute",
                "raw": do
            }

    return {
        "type": root_tag.lower(),
        "version": None,
        "lang": xml_info.get('lang'),
        "source": "root_tag_inferred",
        "raw": root_tag
    }


def is_placeholder_file_attr(file_attr: str, root_tag: str) -> bool:
    """判断 file 属性是否为占位符。"""
    if not file_attr:
        return True
    return file_attr.lower().endswith('.xml') or file_attr.lower() == root_tag.lower()


def find_tiffs_for_xml(xml_name: str, xml_info: Dict[str, Any], tiff_files: List[str]) -> List[str]:
    """为 XML 查找匹配的 TIFF 文件列表，按页码排序。"""
    root_tag = xml_info.get('root_tag', '')
    file_attr = xml_info.get('file_attr', '')

    # 策略 1：按 file_id 精确匹配 TIFF 文件名中包含该 ID 的文件
    if not is_placeholder_file_attr(file_attr, root_tag):
        matched = [t for t in tiff_files if file_attr.lower() in t.lower()]
        if matched:
            return sorted(matched)

    # 策略 2：按 XML 文件名前缀匹配 TIFF 文件名前缀（支持 _1, _2 多页）
    xml_stem = os.path.splitext(xml_name)[0]
    matched = []
    for t in tiff_files:
        t_stem = os.path.splitext(t)[0]
        # 完全匹配 或 前缀匹配（如 WO2017021797_20 对应 WO2017021797_20_1）
        if t_stem == xml_stem or t_stem.startswith(xml_stem + '_'):
            matched.append(t)

    return sorted(matched)


def extract_page_info(tiff_path: str) -> Dict[str, Any]:
    """提取 TIFF 页信息。"""
    with Image.open(tiff_path) as img:
        return {
            "frames": img.n_frames,
            "width": img.width,
            "height": img.height,
            "mode": img.mode
        }


def build_record(xml_name: str, xml_path: str, tiff_paths: List[str], tiff_root: str) -> Dict[str, Any]:
    xml_info = parse_xml_header(xml_path)
    form = parse_form_info(xml_info)

    # 真实 file_id：优先 file 属性，其次 file-reference-id，最后 XML 文件名
    file_id = xml_info.get('file_attr')
    if is_placeholder_file_attr(file_id, xml_info.get('root_tag', '')):
        file_id = xml_info.get('file_reference_id') or os.path.splitext(xml_name)[0]

    pages = []
    for i, tiff_name in enumerate(tiff_paths, start=1):
        tiff_abs = os.path.join(tiff_root, tiff_name)
        page_info = extract_page_info(tiff_abs)
        pages.append({
            "page_no": i,
            "file": tiff_name,
            "image_id": file_id,
            "width": page_info["width"],
            "height": page_info["height"],
            "mode": page_info["mode"],
            "frames": page_info["frames"],
            "sha256": compute_sha256(tiff_abs)
        })

    record_id = os.path.splitext(xml_name)[0]

    return {
        "id": record_id,
        "form": {
            **form,
            "file_id": file_id
        },
        "pages": pages,
        "page_count": len(pages),
        "xml_info": {
            "root_tag": xml_info.get('root_tag'),
            "publication_no": xml_info.get('publication_no'),
            "application_no": xml_info.get('application_no'),
            "file_reference_id": xml_info.get('file_reference_id'),
            "file_attr": xml_info.get('file_attr'),
            "do_attr": xml_info.get('do_attr'),
            "date_produced": xml_info.get('date_produced')
        },
        "provenance": {
            "xml": xml_name,
            "xml_sha256": compute_sha256(xml_path),
            "image_dir": tiff_root,
            "zip": None  # 后续填入真实 zip 名
        },
        "status": {
            "has_images": len(pages) > 0,
            "missing_images": len(pages) == 0,
            "inferred_file_id": is_placeholder_file_attr(xml_info.get('file_attr', ''), xml_info.get('root_tag', ''))
        }
    }


def main():
    xml_root = "./xmlfiles"
    tiff_root = "./tifFiles"
    output_path = "./multimodal_records.json"

    xml_files = sorted([f for f in os.listdir(xml_root) if f.lower().endswith('.xml')])
    tiff_files = sorted([f for f in os.listdir(tiff_root) if f.lower().endswith('.tif')])

    records = []
    for xml_name in xml_files:
        xml_path = os.path.join(xml_root, xml_name)
        matched_tiffs = find_tiffs_for_xml(xml_name, parse_xml_header(xml_path), tiff_files)
        record = build_record(xml_name, xml_path, matched_tiffs, tiff_root)
        records.append(record)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # 生成申请级聚合索引
    application_index: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        app_no = r['xml_info'].get('application_no') or r['xml_info'].get('file_reference_id') or 'unknown'
        application_index.setdefault(app_no, []).append({
            "record_id": r['id'],
            "form_type": r['form']['type'],
            "form_version": r['form']['version'],
            "date_sending": r['xml_info'].get('date_produced'),
            "page_count": r['page_count'],
            "has_images": r['status']['has_images'],
            "publication_no": r['xml_info'].get('publication_no')
        })

    # 按日期排序每个申请下的通知
    for app_no in application_index:
        application_index[app_no].sort(key=lambda x: (x['date_sending'] or '', x['record_id']))

    index_path = "./application_index.json"
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(application_index, f, ensure_ascii=False, indent=2)

    print(f"已生成 {len(records)} 条主记录: {output_path}")
    print(f"已生成申请级聚合索引: {index_path}")
    for r in records:
        status = "有图" if r['status']['has_images'] else "缺图"
        inferred = "(file_id 推断)" if r['status']['inferred_file_id'] else ""
        print(f"  {r['id']}: pages={r['page_count']}, form={r['form']['type']}, status={status} {inferred}")

    print("\n申请级聚合（示例）:")
    for app_no, docs in application_index.items():
        print(f"  申请 {app_no}: {len(docs)} 份通知")
        for d in docs:
            print(f"    - {d['record_id']}: {d['form_type']} ({d['date_sending']})")


if __name__ == "__main__":
    main()