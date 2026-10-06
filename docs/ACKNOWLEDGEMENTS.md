# 致谢与第三方来源

知页是在学习智能体原理与工程实践后，围绕个人期末复习需求实现的项目。感谢以下学习资料、库和模型的作者与维护者。

## 学习资料与 Agent 框架

- [Datawhale Hello-Agents](https://github.com/datawhalechina/hello-agents)：智能体原理与实践学习资料。
- [HelloAgents](https://github.com/jjyaoao/HelloAgents)：项目固定使用 `hello-agents==0.2.9`，引用工具基类和工具注册机制；课程数据管理、检索编排、笔记与练习流程由本项目实现。
- [pi-ai](https://github.com/earendil-works/pi/tree/main/packages/ai)：通过独立 Node.js 桥接提供服务商模型目录和模型调用。

## 核心组件

| 来源 | 在项目中的用途 |
| --- | --- |
| [Vue](https://vuejs.org/) / [Vite](https://vite.dev/) | 复习工作台与前端开发工具 |
| [FastAPI](https://fastapi.tiangolo.com/) | HTTP 接口与 SSE 流式响应 |
| [SQLAlchemy](https://www.sqlalchemy.org/) / [SQLite](https://www.sqlite.org/) | 本机业务数据存储 |
| [Chroma](https://github.com/chroma-core/chroma) | 持久化向量索引 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 本地 CPU 模型推理 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | 图片文字识别 |
| [RapidLaTeXOCR](https://pypi.org/project/rapid-latex-ocr/0.0.9/) | 独立环境中的图片公式识别 |
| [Apache Tika](https://tika.apache.org/) | DOC/PPT/RTF 的正文、表格与内嵌图片提取 |
| [pypdf](https://github.com/py-pdf/pypdf) / [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | PDF 文字读取与页面渲染 |
| [python-docx](https://github.com/python-openxml/python-docx) / [python-pptx](https://github.com/scanny/python-pptx) | DOCX/PPTX 解析 |
| [markdown-it](https://github.com/markdown-it/markdown-it) / [KaTeX](https://katex.org/) | Markdown 与数学公式显示 |

## 向量模型

默认向量模型为 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)。项目使用 [Xenova 提供的 ONNX 导出](https://huggingface.co/Xenova/bge-small-zh-v1.5)，固定快照为 `75c43b069aac4d136ba6bc1122f995fedcfd2781`，加载非量化 FP32 模型。

模型文件由运行环境下载，未放入本仓库。模型使用条件应查阅相应模型卡与许可证。

## 许可范围

此列表说明主要来源，不替代各组件的许可证、版权声明或完整依赖清单。完整版本由根目录和 `formula-worker/` 中的 `uv.lock`、前端和模型桥接目录中的 `package-lock.json` 记录。本项目许可证暂未选择，第三方组件和模型仍遵循各自的许可。
