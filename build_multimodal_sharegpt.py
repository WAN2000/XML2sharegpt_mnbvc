import os
import json
import zipfile
import hashlib
from io import BytesIO
from PIL import Image
from typing import List, Dict, Any


def compute_sha256(data: bytes) -> str:
    """计算数据的 SHA256 哈希值。"""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def extract_tiff_from_zip(zip_path: str, tiff_file: str) -> bytes:
    """从 ZIP 文件中提取指定的 TIFF 文件内容。"""
    with zipfile.ZipFile(zip_path, 'r') as z:
        return z.read(tiff_file)


def convert_tiff_to_png(tiff_bytes: bytes) -> bytes:
    """将 TIFF 图像转换为 PNG 格式。"""
    img = Image.open(BytesIO(tiff_bytes))
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def extract_flat_fields(structured: Dict[str, Any], parent_key: str = "") -> Dict[str, str]:
    """从嵌套的 XML 结构中提取扁平化的字段键值对。"""
    fields = {}
    tag = structured.get("tag", "")

    # 构建当前键名
    current_key = f"{parent_key}.{tag}" if parent_key else tag

    # 提取文本内容
    if "text" in structured and structured["text"]:
        fields[current_key] = structured["text"]

    # 提取属性
    if "attrs" in structured:
        for attr_name, attr_value in structured["attrs"].items():
            if attr_value:
                fields[f"{current_key}@{attr_name}"] = attr_value

    # 递归处理子元素
    if "children" in structured:
        for child in structured["children"]:
            # 处理 items 列表（多个相同标签）
            if "items" in child:
                for idx, item in enumerate(child["items"], 1):
                    child_key = f"{current_key}.{child['tag']}[{idx}]"
                    child_fields = extract_flat_fields(item, child_key)
                    fields.update(child_fields)
            else:
                child_fields = extract_flat_fields(child, current_key)
                fields.update(child_fields)

    return fields


def get_form_description(form_info: Dict[str, Any]) -> str:
    """根据表单信息生成描述文本。"""
    if not form_info:
        return "未知类型的PCT专利文档"

    form_type = form_info.get("type", "unknown").upper()
    version = form_info.get("version", "")
    lang = form_info.get("lang", "").upper()

    # 表单类型描述映射
    form_descriptions = {
        "RO102": "受理通知（Receiving Office Notification）",
        "RO105": "重要通知（申请号及申请日确认）",
        "RO123": "检索单位通知",
        "RO150": "翻译补交通知",
        "IB301": "请求书",
        "IB304": "指定局通知",
        "IB306": "优先权文件",
        "IB308": "国际公布",
        "IB311": "撤回请求",
        "IB326": "进入国家阶段声明",
        "IB373": "更正请求",
        "WRITTEN-OPINION": "书面意见",
        "SEARCH-REPORT": "检索报告"
    }

    desc = form_descriptions.get(form_type, f"Form PCT/{form_type}")

    if version:
        desc += f"，版本：{version}"
    if lang:
        desc += f"，语言：{lang}"

    return desc


def build_conversations(record: Dict[str, Any], image_dir: str) -> List[Dict[str, str]]:
    """构建多模态 ShareGPT 对话。"""
    conversations = []
    form_info = record.get("form")
    structured = record.get("structured")
    tiff_pages = record.get("tiff_pages", [])
    page_count = record.get("page_count", 0)

    # 1. 开场：展示第一页图像
    if tiff_pages:
        png_filename = f"{record['id']}_000001.png"
        conversations.append({
            "from": "human",
            "value": f"请分析这份PCT专利文档的内容。\n\n<img src=\"{png_filename}\" />"
        })

    # 2. GPT回答：基于表单类型和结构化信息
    if form_info:
        form_desc = get_form_description(form_info)
        response = f"这是一份{form_desc}。"

        # 添加基本元数据
        if record.get("application_no"):
            response += f"\n\n国际申请号：{record['application_no']}"
        if record.get("file_reference_id"):
            response += f"\n文件参考ID：{record['file_reference_id']}"
        if record.get("publication_no"):
            response += f"\n公开号：{record['publication_no']}"

        conversations.append({
            "from": "gpt",
            "value": response
        })

    # 3. 字段级问答：基于XML完整结构
    if structured:
        fields = extract_flat_fields(structured)

        # 过滤掉过长的值和系统字段（属性键）
        filtered_fields = {}
        for key, value in fields.items():
            # 跳过纯属性键（如 ro123@date-produced）
            if '@' in key:
                continue
            # 跳过过长的值
            if len(str(value)) > 200:
                continue
            filtered_fields[key] = value

        # 添加字段提取请求
        if filtered_fields:
            conversations.append({
                "from": "human",
                "value": "请提取文档中的关键字段信息。"
            })

            # 构建字段回答
            field_parts = []
            for key, value in filtered_fields.items():
                # 简化键名（去掉前缀）
                simple_key = key.split('.')[-1]
                if simple_key.isdigit():
                    simple_key = key.split('.')[-2] if len(key.split('.')) > 1 else key
                field_parts.append(f"{simple_key}: {value}")

            conversations.append({
                "from": "gpt",
                "value": "\n".join(field_parts)
            })

            # 逐个字段问答（可选，增加训练数据多样性）
            for key, value in list(filtered_fields.items())[:10]:  # 最多10个字段
                simple_key = key.split('.')[-1]
                conversations.append({
                    "from": "human",
                    "value": f"{simple_key}是什么？"
                })
                conversations.append({
                    "from": "gpt",
                    "value": str(value)
                })

    # 4. 多页文档：依次展示后续页面
    if page_count > 1:
        for idx, page in enumerate(tiff_pages[1:], start=2):
            page_num_str = f"{idx:06d}"
            png_filename = f"{record['id']}_{page_num_str}.png"
            conversations.append({
                "from": "human",
                "value": f"请查看第{idx}页内容。\n\n<img src=\"{png_filename}\" />"
            })

            # 第2页及以后的页面描述
            if form_info:
                conversations.append({
                    "from": "gpt",
                    "value": f"这是第{idx}页，继续展示{get_form_description(form_info)}的内容。"
                })

    # 5. 总结（如有多页）
    if page_count > 1:
        conversations.append({
            "from": "human",
            "value": "请总结这份文档的主要内容。"
        })
        if form_info:
            conversations.append({
                "from": "gpt",
                "value": f"这份{get_form_description(form_info)}共{page_count}页，包含了完整的PCT专利申请相关信息。"
            })

    return conversations


def build_multimodal_sharegpt(record: Dict[str, Any], image_dir: str, zip_dir: str) -> Dict[str, Any]:
    """构建完整的多模态 ShareGPT 记录。"""
    # 提取并转换所有 TIFF 图像
    images = []
    tiff_pages = record.get("tiff_pages", [])
    zip_path = os.path.join(zip_dir, record["zip_name"])

    for idx, page in enumerate(tiff_pages, start=1):
        try:
            # 从 ZIP 中提取 TIFF
            tiff_bytes = extract_tiff_from_zip(zip_path, page["file"])

            # 转换为 PNG
            png_bytes = convert_tiff_to_png(tiff_bytes)

            # 生成文件名
            page_num_str = f"{idx:06d}"
            png_filename = f"{record['id']}_{page_num_str}.png"
            png_path = os.path.join(image_dir, png_filename)

            # 保存 PNG 文件
            with open(png_path, 'wb') as f:
                f.write(png_bytes)

            images.append({
                "file": png_filename,
                "page_no": idx,
                "width": page["width"],
                "height": page["height"],
                "sha256": compute_sha256(png_bytes),
                "source_tiff": page["file"]
            })
        except Exception as e:
            print(f"  [警告] 处理 {record['id']} 第{idx}页失败: {e}")

    # 构建对话
    conversations = build_conversations(record, image_dir)

    return {
        "id": record["id"],
        "conversations": conversations,
        "form": record.get("form"),
        "application_no": record.get("application_no"),
        "publication_no": record.get("publication_no"),
        "page_count": record.get("page_count", 0),
        "images": images,
        "provenance": {
            "zip": record["zip_name"],
            "source": "corpus_records.json"
        }
    }


def main():
    # 配置
    corpus_path = "./corpus_records.json"
    zip_dir = "./zipFiles"
    output_dir = "./multimodal_sharegpt"
    image_dir = os.path.join(output_dir, "images")

    # 创建目录
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # 读取语料记录
    with open(corpus_path, 'r', encoding='utf-8') as f:
        corpus = json.load(f)

    records = corpus.get("records", [])
    total_records = len(records)

    print(f"开始生成多模态 ShareGPT 数据...")
    print(f"总记录数: {total_records}")
    print(f"图像输出目录: {image_dir}")
    print(f"ShareGPT输出目录: {output_dir}")
    print("-" * 60)

    # 统计信息
    stats = {
        "total": 0,
        "with_xml": 0,
        "with_images": 0,
        "total_images": 0,
        "errors": []
    }

    # 逐个处理记录
    for i, record in enumerate(records, start=1):
        record_id = record["id"]
        print(f"\n[{i}/{total_records}] 处理 {record_id}...")

        try:
            # 跳过没有 TIFF 图像的记录
            if not record.get("tiff_pages"):
                print(f"  [跳过] 没有 TIFF 图像")
                continue

            # 构建多模态 ShareGPT
            sharegpt_data = build_multimodal_sharegpt(record, image_dir, zip_dir)

            # 保存 ShareGPT JSON 文件
            output_path = os.path.join(output_dir, f"{record_id}_sharegpt.json")
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(sharegpt_data, f, ensure_ascii=False, indent=2)

            # 更新统计
            stats["total"] += 1
            if record.get("xml_info"):
                stats["with_xml"] += 1
            if sharegpt_data["images"]:
                stats["with_images"] += 1
                stats["total_images"] += len(sharegpt_data["images"])

            print(f"  [完成] 生成 {len(sharegpt_data['images'])} 张图像, "
                  f"{len(sharegpt_data['conversations'])} 条对话")

        except Exception as e:
            print(f"  [错误] {e}")
            stats["errors"].append({"id": record_id, "error": str(e)})

    # 输出统计结果
    print("\n" + "-" * 60)
    print("生成完成！")
    print(f"\n统计结果:")
    print(f"  处理记录数: {stats['total']}")
    print(f"  含 XML: {stats['with_xml']}")
    print(f"  含图像: {stats['with_images']}")
    print(f"  生成图像总数: {stats['total_images']}")

    if stats["errors"]:
        print(f"\n错误列表 ({len(stats['errors'])} 个):")
        for err in stats["errors"]:
            print(f"  {err['id']}: {err['error']}")

    # 生成汇总索引
    index = {
        "statistics": stats,
        "generated_files": [
            f"{r['id']}_sharegpt.json" for r in records
            if r.get("tiff_pages")
        ]
    }
    index_path = os.path.join(output_dir, "index.json")
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\n索引文件已生成: {index_path}")


if __name__ == "__main__":
    main()