================================================================================
  PCT专利文档多模态语料构建工具
================================================================================

将包含 XML 和 TIFF 的 ZIP 压缩包转换为图文多模态语料。
  入库推荐写出 MNBVC 通用多模态格式（mm_template_mnbvc BLOCK_SCHEMA）；
  也可仍输出按专利归并的 ShareGPT parquet / JSON。

================================================================================
  功能概述
================================================================================

  - 四层线索关联：基于包级、页级、模板级、字段级四层线索组织 XML 与 TIFF 文件
  - 完整 XML 结构化：递归解析 XML 嵌套结构，保留标签、属性、文本及子元素关系
  - 多模态对话构建：每张图像与对应的结构化数据结合，构建图文问答对
  - 转为多模态格式：按专利归并后可写出 mm_template（一块一行、图为
    struct<bytes,path>），与 Chinaxiv 等 MNBVC 多模态集合 schema 对齐
  - 表单类型识别：自动识别 PCT 表单类型（RO102、RO105、IB301、IB304 等）

================================================================================
  环境要求
================================================================================

  Python 3.8+
  依赖库：Pillow（用于 TIFF 到 PNG 转换）
           pyarrow、pandas（仅按专利归并 Parquet 管线需要）

  安装依赖：
    pip install Pillow pyarrow pandas

================================================================================
  项目文件说明
================================================================================

  核心脚本：
    build_corpus_records.py      解析 ZIP 包，生成语料记录（第一步）
    build_multimodal_sharegpt.py  构建多模态 ShareGPT 数据（第二步，主脚本）
    build_patent_parquet.py       按专利归并各时期文件，生成 Parquet（--format sharegpt|mm）
    export_mm_template.py         把已有 ShareGPT parquet 转成 mm_template 并对照
    read_parquet.py               查看专利 Parquet 内容（只读查看工具，仅依赖 pyarrow）
    verify_patent_parquet.py      校验 / 导出专利 Parquet
    build_multimodal_records.py   构建多模态记录索引
    build_multimodal_corpus.py    构建多模态语料
    inspect_zip_files.py          检查 ZIP 文件内容
    main.py                       入口文件

  输入数据目录（需自行准备）：
    zipFiles/                     存放 PCT 文档 ZIP 压缩包
    tifFiles/                     直接存放 TIFF 图像文件（可选）
    xmlfiles/                     直接存放 XML 文件（可选）
    专利 zip 源目录（默认）：
    H:\BaiduNetdiskDownload\random_1000_patents/

  输出目录（脚本运行后自动生成）：
    corpus_records.json           语料记录中间文件
    multimodal_sharegpt/          最终输出目录
      images/                     PNG 图像文件
      *_sharegpt.json             ShareGPT 对话文件
      index.json                  汇总索引
    patent_parquet/               ShareGPT 格式输出（--format sharegpt，默认）
      WO*.parquet                 每个专利一个文件（1 行，内嵌页面图像 + 对话）
      index.json                  汇总索引
    mm_template_parquet/          mm_template 格式输出（--format mm）
      WO*.parquet                 每个专利一个文件（一块一行，HF Image struct）
      index.json                  汇总索引

================================================================================
  输入数据格式
================================================================================

  1. ZIP 文件命名规范（放入 zipFiles/ 目录）：
     WO2017021797_10.zip  -> 公开号 + 卷内文档序号
     前面的数字部分代表专利号，'_' 后面的数字代表该专利的不同流程文档序号

  2. 每个 ZIP 包应包含：
     - 一个 XML 文件（如 ro102.xml、ib301.xml 等）
     - 一个或多个 TIFF 图像文件（如 000001.tif、000002.tif）

  3. ZIP 包目录结构示例：
     WO2017021797_20.zip
       ├── ro102.xml
       ├── 000001.tif
       └── 000002.tif

================================================================================
  使用步骤
================================================================================

  第一步：准备数据
    将 PCT 文档 ZIP 压缩包放入项目根目录下的 zipFiles/ 文件夹中。
    如果已有直接的 XML 和 TIFF 文件，可分别放入 xmlfiles/ 和 tifFiles/ 目录。

  第二步：生成语料记录
    运行以下命令解析所有 ZIP 包，生成语料记录：
    
      python build_corpus_records.py
    
    输出文件：corpus_records.json（包含所有 ZIP 包的结构化 XML、TIFF 元数据等）

  第三步：构建多模态 ShareGPT 数据
    运行以下命令将语料转换为多模态 ShareGPT 格式：
    
      python build_multimodal_sharegpt.py
    
    输出目录：multimodal_sharegpt/
    
    运行过程中会：
    - 从 ZIP 中提取 TIFF 图像并转换为 PNG 格式
    - 基于 XML 结构化数据构建对话
    - 生成 ShareGPT 格式的 JSON 文件

================================================================================
  输出格式说明
================================================================================

  生成的 ShareGPT JSON 文件结构如下：

    {
      "id": "WO2017021797_20",
      "conversations": [
        {
          "from": "human",
          "value": "请分析这份PCT专利文档的内容。\n\n<img src=\"WO2017021797_20_000001.png\" />"
        },
        {
          "from": "gpt",
          "value": "这是一份受理通知（Receiving Office Notification），版本：201507，语言：EN。"
        },
        {
          "from": "human",
          "value": "请提取文档中的关键字段信息。"
        },
        {
          "from": "gpt",
          "value": "applicant: ZANETTIN, Gianluigi\ncountry: IB\ndoc-number: IB2016053773"
        },
        {
          "from": "human",
          "value": "applicant是什么？"
        },
        {
          "from": "gpt",
          "value": "ZANETTIN, Gianluigi"
        }
      ],
      "form": {
        "type": "ro102",
        "version": "201507",
        "lang": "en"
      },
      "images": [
        {"file": "WO2017021797_20_000001.png", "page_no": 1},
        {"file": "WO2017021797_20_000002.png", "page_no": 2}
      ]
    }

  对话结构说明：
    - 对话 1：展示第一页图像，GPT 识别文档类型和基本信息
    - 对话 2：提取关键字段信息（批量提取）
    - 对话 3+：逐个字段问答（最多 10 个字段）
    - 多页文档：依次展示后续页面
    - 最后：总结文档内容

================================================================================
  支持的表单类型
================================================================================

  表单类型      说明
  ----------------------------------------------------------------
  RO102         受理通知（Receiving Office Notification）
  RO105         重要通知（申请号及申请日确认）
  RO123         检索单位通知
  RO150         翻译补交通知
  IB301         请求书
  IB304         指定局通知
  IB306         优先权文件
  IB308         国际公布
  IB311         撤回请求
  IB326         进入国家阶段声明
  IB373         更正请求
  written-opinion    书面意见
  search-report      检索报告

================================================================================
  操作指引：ZIP → Parquet（按专利归并）
================================================================================

  功能：扫描专利 zip 源目录（文件名形如 WO2014139619.zip、WO2014139619_1.zip，
  下划线前为专利号，下划线后的数字为该专利的不同时期文件），把同一专利的全部
  时期文档按文档内日期排序后归并，生成"每专利一个 Parquet 文件"。

  步骤一：准备环境（首次使用）
    安装依赖（生成 Parquet 需要 pyarrow、pandas）：

      pip install Pillow pyarrow "pandas>=2.3,<3.0"

    说明：若本机 NumPy 是 2.x，pyarrow 需 >=16.1 且 pandas 需 >=2.3，否则会报
    "_ARRAY_API not found" 之类的二进制不兼容错误。

  步骤二：小样本试跑（建议先用少量专利验证）
    运行：

      python build_patent_parquet.py --patents WO2014139619,WO2017021797,WO2014140987

    预期：patent_parquet/ 目录下生成 3 个 ShareGPT .parquet 文件和 index.json。

    改出 mm_template（一块一行，兼容 MNBVC 通用多模态格式）：

      python build_patent_parquet.py --format mm --patents WO2014139619,WO2017021797,WO2014140987

    预期：mm_template_parquet/ 目录下生成 3 个 .parquet。已有 ShareGPT parquet
    也可事后转换：python export_mm_template.py --src ./patent_parquet

  步骤三：查看与校验结果
    列出全部 parquet 概要：

      python read_parquet.py

    查看单个专利详情（--view 可选 summary / schema / docs / conversations / images）：

      python read_parquet.py --patent WO2017021797 --view summary
      python read_parquet.py --patent WO2017021797 --view schema
      python read_parquet.py --patent WO2017021797 --view docs
      python read_parquet.py --patent WO2017021797 --view conversations --max-turns 20
      python read_parquet.py --patent WO2017021797 --view images

    把对话文本和 PNG 图像导出到文件夹，直接翻阅（生成 conversation.json /
    conversation.txt / images/*.png）：

      python verify_patent_parquet.py --patent WO2017021797 --export-dir ./parquet_preview

    完整性校验（与源 zip 数、旧管线页数、对话交替等交叉核对；未安装 Pillow 时
    会自动跳过 PNG 解码检查）：

      python verify_patent_parquet.py --verify

  步骤四：全量转换（确认无误后执行）
    运行：

      python build_patent_parquet.py

    说明：源目录共约 1000 个专利、2 万个 zip，预计耗时数小时；输出总体积约等于
    PNG 总量（数十 GB 量级），请预留磁盘空间。

  build_patent_parquet.py 可选参数：
    --format    输出格式：sharegpt（默认）或 mm
    --zip-dir   专利 zip 源目录（默认 H:\BaiduNetdiskDownload\random_1000_patents）
    --out-dir   输出目录（默认 sharegpt -> ./patent_parquet，mm -> ./mm_template_parquet）
    --patents   逗号分隔的专利号白名单（只处理这些专利）
    --limit     最多处理的专利数

  --format sharegpt 输出：patent_parquet/WO{专利号}.parquet，每个文件 1 行，主要字段：
    patent_no / application_no / publication_no  专利与申请标识
    n_docs / n_pages / n_images                  文件数、页数、图像数
    docs           各时期文档列表（序号、zip 名、kind、日期、表单、页数等）
    conversations  归并后的整条 ShareGPT 对话（human/gpt 交替，格式与现有管线一致）
    images         页面图像元数据（文件名、宽高、sha256 等）
    image_bytes    内嵌的 PNG 页面字节（与 images 一一对应）

  --format mm 输出：mm_template_parquet/WO{专利号}.parquet，一块一行，schema
  对齐 mm_template_mnbvc BLOCK_SCHEMA（实体ID/块类型/页ID/文本/图片 struct 等）。
  一页一个 image-text-pair；无图但有 XML 的文件另出 text 块。mm 路径不生成 ShareGPT 对话。

================================================================================
  转为 MNBVC 多模态格式（mm_template）
================================================================================

  目标：把「一件专利一行、内嵌对话 + list<binary> 图」转成 MNBVC 通用多模态
  格式。规范见 https://github.com/MIracleyin/mm_template_mnbvc 的 BLOCK_SCHEMA。
  入库、与其它多模态集合合并、HuggingFace 读图，都用这一路。

  两条路径（结果 schema 相同）：

    1) ZIP 直出（推荐，不经过 ShareGPT 对话）：

         python build_patent_parquet.py --format mm

       默认写到 mm_template_parquet/。mm 路径从 XML 抽正文，不生成 human/gpt 对话。

    2) 已有 ShareGPT parquet 事后转换：

         python export_mm_template.py --src . --out-dir ./mm_template_parquet
         python export_mm_template.py --compare-only

       从 conversations 回填有信息量的文本，并核对图像字节是否一致。

  粒度：一件专利一个实体，一页一个 image-text-pair 块；无图但有 XML 的文件
  （检索报告、书面意见）另出 text 块。

  字段对应：

    当前 ShareGPT parquet              mm_template
    ---------------------------------  --------------------------------
    1 专利 = 1 行                      1 页/块 = 1 行
    patent_no                          实体ID（每块重复）
    application_no / publication_no    扩展字段 JSON
    docs[] / n_docs / n_pages          扩展字段 JSON
    conversations[]                    文本（只留 XML/表单正文，丢掉填充问答）
    images[] + image_bytes[]           图片 struct<bytes, path>
    images.page_no（每份 zip 从 1 起）  页ID（专利内从 0 起连续）
    images.sha256                      扩展字段.sha256；列 md5 为内容 md5
    docs.pub_date                      时间（YYYYMMDD）
    （无）                             块类型 / 块ID / 视频 / 音频 / OCR / STT

  mm 一块示例（image-text-pair）：

    实体ID   WO2017021797
    块类型   image-text-pair
    页ID     0
    时间     20160628
    图片     {bytes: PNG, path: "WO2017021797_17_000001.png"}
    文本     这是一份请求书…… + XML 关键字段
    扩展字段 {"patent_no","zip_name","form_type","sha256","source_tiff",...}

  全量入库：

      python build_patent_parquet.py --format mm

================================================================================
  数据处理流程
================================================================================

  ZIP 文件
    │
    ├─ 旧管线（按 zip 出 ShareGPT JSON）
    │     build_corpus_records.py → corpus_records.json
    │     → build_multimodal_sharegpt.py → multimodal_sharegpt/
    │
    └─ 按专利归并 Parquet
          build_patent_parquet.py --format sharegpt  → patent_parquet/   （1 专利 1 行）
          build_patent_parquet.py --format mm        → mm_template_parquet/  （多模态格式，1 页 1 块）
          已有 sharegpt parquet 也可：
            export_mm_template.py --src ./patent_parquet → mm_template_parquet/

================================================================================
  运行示例
================================================================================

  PS H:\mnbvc\xmltosg> python build_corpus_records.py

  已生成 27 条语料记录 -> corpus_records.json

  统计:
    zip 总数: 27
    含 XML: 14
    含 TIFF: 24
    总页数: 161

  表单类型分布:
    ro102: 1
    ro105: 1
    ...

  PS H:\mnbvc\xmltosg> python build_multimodal_sharegpt.py

  开始生成多模态 ShareGPT 数据...
  [1/27] 处理 WO2017021797_10...
    [完成] 生成 1 张图像, 1 条对话
  [12/27] 处理 WO2017021797_20...
    [完成] 生成 2 张图像, 28 条对话
  ...
  生成完成！

  统计结果:
    处理记录数: 24
    含 XML: 12
    含图像: 24
    生成图像总数: 161

================================================================================
  Git 上传说明
================================================================================

  需要上传到仓库的文件：
    build_corpus_records.py
    build_multimodal_sharegpt.py
    build_patent_parquet.py
    export_mm_template.py
    read_parquet.py
    verify_patent_parquet.py
    build_multimodal_records.py
    build_multimodal_corpus.py
    inspect_zip_files.py
    main.py
    .gitignore
    readme.txt

  不应上传的文件（已在 .gitignore 中排除）：
    zipFiles/          - ZIP 原始数据（体积大）
    tifFiles/          - TIFF 图像文件
    xmlfiles/          - XML 文件
    multimodal_sharegpt/  - 生成的输出文件
    patent_parquet/    - 生成的专利 Parquet 文件
    mm_template_parquet/ - 生成的 mm_template Parquet 文件
    sharegpt_output/   - 旧版输出文件
    *.json             - 各种生成的记录文件
    footer_samples/    - 页脚样本图片

================================================================================
  常见问题
================================================================================

  Q: 运行报错 "No such file or directory: corpus_records.json"
  A: 请先运行 build_corpus_records.py 生成该文件。

  Q: 某些 ZIP 显示 [跳过] 没有 TIFF 图像
  A: 这些 ZIP 包中不包含 TIFF 文件，无法生成多模态数据，属于正常情况。

  Q: 某些文档对话条数很少（只有 1 条）
  A: 该 ZIP 包中没有 XML 文件，无法生成结构化字段问答，只有图像展示。

  Q: 如何添加新的 ZIP 文件？
  A: 直接将新 ZIP 放入 zipFiles/ 目录，重新运行两个脚本即可。

  Q: 运行 build_patent_parquet.py 报 NumPy 相关错误（如 "_ARRAY_API not found"）
  A: pyarrow/pandas 与 NumPy 2.x 存在二进制兼容要求，请升级依赖：
     pip install --upgrade pyarrow "pandas>=2.3,<3.0"

================================================================================