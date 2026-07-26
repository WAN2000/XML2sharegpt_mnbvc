import os
import json
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict


def analyze_zip(zip_path: str) -> dict:
    """分析单个 zip 包的内容。"""
    result = {
        "zip_name": os.path.basename(zip_path),
        "xml_files": [],
        "tiff_files": [],
        "other_files": []
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            for info in z.infolist():
                name = info.filename
                if name.lower().endswith('.xml'):
                    result["xml_files"].append(name)
                elif name.lower().endswith(('.tif', '.tiff')):
                    result["tiff_files"].append(name)
                else:
                    result["other_files"].append(name)
    except zipfile.BadZipFile as e:
        result["error"] = str(e)
        return result

    # 读取第一个 XML 的根标签信息
    if result["xml_files"]:
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                xml_content = z.read(result["xml_files"][0])
                root = ET.fromstring(xml_content)
                result["xml_root"] = {
                    "tag": root.tag,
                    "file_attr": root.attrib.get('file'),
                    "do_attr": root.attrib.get('do'),
                    "lang": root.attrib.get('lang'),
                    "date_produced": root.attrib.get('date-produced'),
                    "date_sending": root.attrib.get('date-sending'),
                    "status": root.attrib.get('status')
                }
                # 尝试读取 application reference
                file_ref = root.find('file-reference-id')
                app_ref = root.find('.//application-reference/document-id/doc-number')
                pub_ref = root.find('.//publication-reference/document-id/doc-number')
                result["xml_root"]["file_reference_id"] = file_ref.text if file_ref is not None else None
                result["xml_root"]["application_no"] = app_ref.text if app_ref is not None else None
                result["xml_root"]["publication_no"] = pub_ref.text if pub_ref is not None else None
        except Exception as e:
            result["xml_parse_error"] = str(e)

    return result


def main():
    zip_dir = "./zipFiles"
    zip_files = sorted([f for f in os.listdir(zip_dir) if f.lower().endswith('.zip')])

    all_results = []
    stats = defaultdict(int)

    for zip_name in zip_files:
        zip_path = os.path.join(zip_dir, zip_name)
        info = analyze_zip(zip_path)
        all_results.append(info)

        stats["total_zips"] += 1
        stats["total_xml"] += len(info.get("xml_files", []))
        stats["total_tiff"] += len(info.get("tiff_files", []))

    # 输出汇总
    summary = {
        "statistics": dict(stats),
        "records": all_results
    }

    output_path = "./zip_analysis.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"已分析 {len(zip_files)} 个 zip 文件")
    print(f"总计 XML: {stats['total_xml']}, TIFF: {stats['total_tiff']}")
    print(f"详细结果: {output_path}\n")

    # 打印每个 zip 的关键信息
    print("Zip 文件内容概览:")
    print("-" * 80)
    for info in all_results:
        root_info = info.get("xml_root", {})
        form = root_info.get('do_attr') or root_info.get('tag', 'unknown')
        xml_count = len(info.get("xml_files", []))
        tiff_count = len(info.get("tiff_files", []))
        print(f"{info['zip_name']}: {form}, XML={xml_count}, TIFF={tiff_count}, file_id={root_info.get('file_attr')}")


if __name__ == "__main__":
    main()