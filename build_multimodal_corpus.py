import os
import re
import json
import zipfile
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from PIL import Image


# 已知的 PCT 通知书表单根标签
NOTIFICATION_TAGS = {
    'ib301', 'ib304', 'ib306', 'ib308', 'ib311', 'ib326', 'ib373',
    'ro102', 'ro105', 'ro123', 'ro150'
}

# 报告类根标签
REPORT_TAGS = {'written-opinion', 'search-report'}

# page-count 类
PAGE_COUNT_TAGS = {'page-count'}


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_form_xml(root: ET.Element) -> Dict[str, Any]:
    """解析表单类 XML。"""
    do_attr = root.attrib.get('do', '')
    form_type = root.tag.lower()
    form_version = None
    form_lang = root.attrib.get('lang')

    if '_' in do_attr:
        parts = do_attr.split('_')
        if len(parts) >= 3:
            form_type = parts[0].lower()
            form_version = parts[1]
            form_lang = parts[2]
        elif len(parts) == 2:
            form_type = parts[0].lower()
            form_version = parts[1]

    file_ref = root.find('file-reference-id')
    app_ref = root.find('.//application-reference/document-id/doc-number')
    pub_ref = root.find('.//publication-reference/document-id/doc-number')

    return {
        "tag": root.tag,
        "form_type": form_type,
        "form_version": form_version,
        "form_lang": form_lang,
        "do_attr": do_attr or None,
        "file_attr": root.attrib.get('file') or None,
        "date_produced": root.attrib.get('date-produced') or None,
        "date_sending": root.attrib.get('date-sending') or None,
        "status": root.attrib.get('status') or None,
        "file_reference_id": file_ref.text if file_ref is not None else None,
        "application_no": normalize_application_no(app_ref.text if app_ref is not None else None),
        "publication_no": pub_ref.text if pub_ref is not None else None,
        "raw_attrs": dict(root.attrib)
    }


def parse_page_count_xml(root: ET.Element) -> Dict[str, Any]:
    """解析 page-count XML。"""
    masters = []
    total_pages = 0
    for pm in root.findall('page-master'):
        count = int(pm.attrib.get('count', 0))
        masters.append({
            "name": pm.attrib.get('name'),
            "count": count,
            "file": pm.attrib.get('file')
        })
        if pm.attrib.get('name', '').lower() == 'document':
            total_pages = count

    return {
        "tag": root.tag,
        "page_masters": masters,
        "total_pages": total_pages
    }


def parse_report_xml(root: ET.Element) -> Dict[str, Any]:
    """解析 written-opinion / search-report XML。"""
    file_ref = root.find('.//file-reference-id')
    app_ref = root.find('.//application-reference/document-id/doc-number')
    pub_ref = root.find('.//publication-reference/document-id/doc-number')
    applicant = root.find('.//applicant-name/name')
    completion = root.find('.//completion-date/date')
    mailing = root.find('.//date-of-mailing/date')

    return {
        "tag": root.tag,
        "form_type": root.tag.lower(),
        "form_version": root.attrib.get('form-version'),
        "form_lang": root.attrib.get('lang'),
        "file_attr": root.attrib.get('file') or None,
        "date_produced": root.attrib.get('date-produced') or None,
        "date_sending": root.attrib.get('date-sending') or None,
        "status": root.attrib.get('status') or None,
        "file_reference_id": file_ref.text if file_ref is not None else None,
        "application_no": normalize_application_no(app_ref.text if app_ref is not None else None),
        "publication_no": pub_ref.text if pub_ref is not None else None,
        "applicant_name": applicant.text if applicant is not None else None,
        "completion_date": completion.text if completion is not None else None,
        "mailing_date": mailing.text if mailing is not None else None,
        "raw_attrs": dict(root.attrib)
    }


def normalize_application_no(app_no: Optional[str]) -> Optional[str]:
    """统一 application_no 格式。"""
    if not app_no:
        return None
    # PCT/IB2016/053773 -> IB2016053773
    m = re.match(r'PCT/([A-Z]{2})(\d{4})/(\d+)', app_no)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3).zfill(6)}"
    # 纯数字 -> 补 IB 前缀
    if app_no.isdigit():
        return f"IB{app_no}"
    return app_no


def parse_std_form_xml(root: ET.Element) -> Dict[str, Any]:
    """解析标准版 wo-form-ro / wo-form-ib XML。"""
    form_id = root.attrib.get('form-id', '')  # e.g. PCT/RO/123, PCT/IB/306
    form_type = None
    if form_id:
        parts = form_id.split('/')
        if len(parts) >= 3:
            form_type = parts[-2].lower() + parts[-1]  # ro123 / ib306

    file_ref = root.find('.//file-reference-id')
    app_ref = root.find('.//application-reference/document-id/doc-number')
    pub_ref = root.find('.//publication-reference/document-id/doc-number')
    title = root.find('.//invention-title')

    return {
        "tag": root.tag,
        "form_type": form_type,
        "form_version": root.attrib.get('form-version'),
        "form_lang": root.attrib.get('lang'),
        "form_id": form_id,
        "file_attr": root.attrib.get('file') or None,
        "date_produced": root.attrib.get('date-produced') or None,
        "date_sending": None,
        "status": root.attrib.get('status') or None,
        "file_reference_id": file_ref.text if file_ref is not None else None,
        "application_no": normalize_application_no(app_ref.text if app_ref is not None else None),
        "publication_no": pub_ref.text if pub_ref is not None else None,
        "invention_title": title.text if title is not None else None,
        "raw_attrs": dict(root.attrib)
    }


def parse_xml_in_zip(xml_content: bytes) -> Optional[Dict[str, Any]]:
    """解析 zip 中的 XML 内容。"""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        return {"error": f"ParseError: {e}"}

    tag = root.tag.lower()
    if tag in NOTIFICATION_TAGS or tag.startswith(('ib', 'ro')):
        return parse_form_xml(root)
    elif tag in ('wo-form-ro', 'wo-form-ib'):
        return parse_std_form_xml(root)
    elif tag in REPORT_TAGS:
        return parse_report_xml(root)
    elif tag in PAGE_COUNT_TAGS:
        return parse_page_count_xml(root)
    else:
        return {"tag": root.tag, "unknown": True, "raw_attrs": dict(root.attrib)}


def classify_zip(xml_parses: List[Dict], tiff_count: int, other_files: List[str]) -> str:
    """根据内容判断 zip 文档类型。"""
    if not xml_parses and tiff_count == 0 and other_files:
        return "attachment"

    if not xml_parses and tiff_count > 0:
        return "image-only"

    tags = {p.get('tag', '').lower() for p in xml_parses}

    if PAGE_COUNT_TAGS & tags:
        return "application-body"

    if REPORT_TAGS & tags:
        return "report"

    if NOTIFICATION_TAGS & tags or any(t.startswith(('ib', 'ro')) for t in tags):
        return "notification"

    return "unknown"


def analyze_zip(zip_path: str) -> Dict[str, Any]:
    """完整分析一个 zip 包。"""
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
        "zip_sha256": compute_sha256_file(zip_path),
        "doc_type": None,
        "xml_files": [],
        "tiff_files": [],
        "other_files": [],
        "page_count": 0,
        "application_no": None,
        "file_reference_id": None,
        "forms": []
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            # 分类文件
            for info in z.infolist():
                name = info.filename
                if name.lower().endswith('.xml'):
                    result["xml_files"].append({"name": name, "size": info.file_size})
                elif name.lower().endswith(('.tif', '.tiff')):
                    result["tiff_files"].append({"name": name, "size": info.file_size})
                else:
                    result["other_files"].append({"name": name, "size": info.file_size})

            # 解析 XML
            xml_parses = []
            for xf in result["xml_files"]:
                content = z.read(xf["name"])
                parsed = parse_xml_in_zip(content)
                parsed["file_name"] = xf["name"]
                parsed["sha256"] = compute_sha256_bytes(content)
                parsed["size"] = xf["size"]
                parsed["is_std_variant"] = '_std' in xf["name"].lower()
                xml_parses.append(parsed)

            # 解析 TIFF 页信息
            pages = []
            for tf in sorted(result["tiff_files"], key=lambda x: x["name"]):
                content = z.read(tf["name"])
                with Image.open(BytesIO(content)) as img:  # 需要 BytesIO
                    pages.append({
                        "file": tf["name"],
                        "sha256": compute_sha256_bytes(content),
                        "size": tf["size"],
                        "frames": img.n_frames,
                        "width": img.width,
                        "height": img.height,
                        "mode": img.mode
                    })

            result["xml_parses"] = xml_parses
            result["pages"] = pages
            result["page_count"] = sum(p["frames"] for p in pages)

            # 确定文档类型
            result["doc_type"] = classify_zip(xml_parses, len(result["tiff_files"]),
                                              [o["name"] for o in result["other_files"]])

            # 提取通用字段并归一化 application_no
            for p in xml_parses:
                app_no = p.get('application_no')
                if app_no and not result["application_no"]:
                    # 统一补全 IB 前缀，例如 2016053773 -> IB2016053773
                    if app_no.isdigit():
                        app_no = f"IB{app_no}"
                    result["application_no"] = app_no
                if p.get('file_reference_id') and not result["file_reference_id"]:
                    result["file_reference_id"] = p['file_reference_id']
                if p.get('form_type'):
                    result["forms"].append({
                        "type": p['form_type'],
                        "version": p.get('form_version'),
                        "lang": p.get('form_lang'),
                        "file_name": p['file_name'],
                        "is_std": p.get('is_std_variant', False)
                    })

            # page-count 的特殊统计
            for p in xml_parses:
                if p.get('tag', '').lower() == 'page-count':
                    result["page_count_meta"] = {
                        "total_pages": p.get('total_pages'),
                        "masters": p.get('page_masters')
                    }

    except zipfile.BadZipFile as e:
        result["error"] = f"BadZipFile: {e}"

    return result


from io import BytesIO


def main():
    zip_dir = "./zipFiles"
    zip_files = sorted([f for f in os.listdir(zip_dir) if f.lower().endswith('.zip')])

    records = []
    stats = {}
    for zip_name in zip_files:
        zip_path = os.path.join(zip_dir, zip_name)
        record = analyze_zip(zip_path)
        records.append(record)
        doc_type = record.get('doc_type') or 'error'
        stats[doc_type] = stats.get(doc_type, 0) + 1

    # 公开号 -> application_no 映射推断
    pub_to_app: Dict[str, str] = {}
    for r in records:
        pub = r.get('publication_no')
        app = r.get('application_no')
        if pub and app and not pub_to_app.get(pub):
            pub_to_app[pub] = app

    # 为 application_no 未知的记录做推断
    for r in records:
        if not r.get('application_no') and r.get('publication_no') in pub_to_app:
            r['application_no'] = pub_to_app[r['publication_no']]
            r['application_no_inferred'] = True

    # 申请级聚合索引
    app_index: Dict[str, List[Dict]] = {}
    for r in records:
        app_no = r.get('application_no') or r.get('file_reference_id') or 'unknown'
        app_index.setdefault(app_no, []).append({
            "record_id": r['id'],
            "doc_type": r['doc_type'],
            "forms": [f['type'] for f in r['forms']],
            "sequence_no": r['sequence_no'],
            "page_count": r['page_count'],
            "publication_no": r['publication_no'],
            "application_no_inferred": r.get('application_no_inferred', False)
        })

    for app_no in app_index:
        app_index[app_no].sort(key=lambda x: (x['sequence_no'] or 0, x['record_id']))

    # 公开号级聚合索引
    pub_index: Dict[str, List[Dict]] = {}
    for r in records:
        pub = r.get('publication_no') or 'unknown'
        pub_index.setdefault(pub, []).append({
            "record_id": r['id'],
            "doc_type": r['doc_type'],
            "forms": [f['type'] for f in r['forms']],
            "sequence_no": r['sequence_no'],
            "page_count": r['page_count'],
            "application_no": r.get('application_no'),
            "application_no_inferred": r.get('application_no_inferred', False)
        })

    for pub in pub_index:
        pub_index[pub].sort(key=lambda x: (x['sequence_no'] or 0, x['record_id']))

    output = {
        "statistics": {
            "total_zips": len(records),
            "doc_type_counts": stats,
            "total_xml_files": sum(len(r['xml_files']) for r in records),
            "total_tiff_files": sum(len(r['tiff_files']) for r in records),
            "total_pages": sum(r['page_count'] for r in records)
        },
        "records": records,
        "application_index": app_index,
        "publication_index": pub_index
    }

    with open('./multimodal_corpus.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已分析 {len(records)} 个 zip 包")
    print(f"文档类型统计: {stats}")
    print(f"总 XML: {output['statistics']['total_xml_files']}, "
          f"总 TIFF: {output['statistics']['total_tiff_files']}, "
          f"总页数: {output['statistics']['total_pages']}")
    print(f"结果保存至: ./multimodal_corpus.json")

    print("\n按申请聚合:")
    for app_no, docs in app_index.items():
        inferred_count = sum(1 for d in docs if d['application_no_inferred'])
        print(f"  {app_no}: {len(docs)} 个 zip (推断 {inferred_count})")
        for d in docs:
            forms = ','.join(d['forms']) if d['forms'] else d['doc_type']
            flag = "(推断)" if d['application_no_inferred'] else ""
            print(f"    - {d['record_id']} ({d['sequence_no']}): {forms}, pages={d['page_count']} {flag}")


if __name__ == "__main__":
    main()