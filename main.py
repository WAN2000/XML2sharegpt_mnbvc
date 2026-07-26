import os
import json
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional


class XMLToShareGPTConverter:
    def __init__(
        self,
        xml_root_dir: str,
        output_dir: str,
        system_prompt: Optional[str] = None
    ):
        self.xml_root_dir = xml_root_dir
        self.output_dir = output_dir
        self.system_prompt = system_prompt

    def _find_files_recursive(self, root_dir: str, extensions: tuple) -> List[str]:
        file_list = []
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.lower().endswith(extensions):
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, root_dir)
                    file_list.append(rel_path)
        return file_list

    def _parse_xml_recursive(self, element: ET.Element) -> List[Dict[str, str]]:
        results = []

        if element.text and element.text.strip():
            results.append({"tag": element.tag, "text": element.text.strip()})

        for child in element:
            results.extend(self._parse_xml_recursive(child))

        return results

    def _parse_xml(self, xml_path: str) -> Dict[str, Any]:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            tag_list = self._parse_xml_recursive(root)
            return {
                "root_tag": root.tag,
                "root_attributes": root.attrib,
                "tag_list": tag_list
            }
        except ET.ParseError as e:
            print(f"解析XML文件失败 {xml_path}: {e}")
            return {}

    def _convert_single_xml(self, xml_rel_path: str) -> Optional[Dict]:
        xml_abs_path = os.path.join(self.xml_root_dir, xml_rel_path)
        xml_data = self._parse_xml(xml_abs_path)
        if not xml_data:
            return None

        conversations = []
        for tag_info in xml_data.get("tag_list", []):
            conversations.append({"from": "human", "value": tag_info["tag"]})
            conversations.append({"from": "gpt", "value": tag_info["text"]})

        sample = {"conversations": conversations}
        if self.system_prompt:
            sample["system"] = self.system_prompt

        return sample

    def convert(self):
        print(f"开始扫描目录: {self.xml_root_dir}")
        xml_files = self._find_files_recursive(self.xml_root_dir, ('.xml',))
        print(f"找到 {len(xml_files)} 个XML文件。")

        os.makedirs(self.output_dir, exist_ok=True)

        for xml_rel_path in xml_files:
            sample = self._convert_single_xml(xml_rel_path)
            if sample:
                xml_name = os.path.splitext(xml_rel_path)[0]
                output_path = os.path.join(self.output_dir, f"{xml_name}_sharegpt.json")
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(sample, f, ensure_ascii=False, indent=2)
                print(f"已生成: {output_path}")

        print(f"转换完成！共生成 {len(xml_files)} 个文件，保存至 {self.output_dir}")


if __name__ == "__main__":
    converter = XMLToShareGPTConverter(
        xml_root_dir="./xmlfiles",
        output_dir="./sharegpt_output",
        system_prompt="你是一个专业的专利文档解析助手。"
    )
    converter.convert()