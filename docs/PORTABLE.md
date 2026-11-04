# 知页 Windows 便携版

适用于 Windows 10/11 64 位。无需安装 Python、Node.js、Java 或 npm。主程序包含运行环境、前端页面、文字 OCR 模型和 Tika；Granite 向量模型和公式 OCR 模型放在单独的模型包里，只需下载一次。模型聊天、生成笔记、出题及订阅登录仍需联网并使用你自己的模型服务。

## 使用

1. 从 GitHub Releases 下载主程序 `Zhiye-版本-windows-x64.zip` 和模型包 `Zhiye-models-编号.zip`，把两个都**完整解压**到同一个普通本机目录，不要在压缩包内运行。解压后该目录下应有 `Zhiye-版本-windows-x64` 和 `Zhiye-models-编号` 两个文件夹。
2. 双击主程序文件夹里的 **启动知页.cmd**。首次启动会把模型复制到 `%LOCALAPPDATA%\Zhiye\models`，需要几十秒；之后可以删除模型包文件夹。等待浏览器打开 `http://127.0.0.1:8000/`。
3. 在右上角“模型配置”添加你自己的模型服务；然后创建课程、上传资料。
4. 使用期间保持启动窗口打开。退出时先等待上传或生成任务结束，再在窗口按 `Ctrl+C`。

若浏览器没有打开，可手动访问上述地址。启动失败时窗口会保留错误；详细日志在 `%LOCALAPPDATA%\Zhiye\logs`。8000 端口被开发版或其他程序占用时，请先关闭占用程序。该包使用浏览器界面，并没有独立桌面窗口。

## 数据与升级

课程、上传文件、会话、练习进度、API Key 和订阅凭据保存在 `%LOCALAPPDATA%\Zhiye\data`，不保存在解压目录。备份时复制整个 `%LOCALAPPDATA%\Zhiye`，其中包含你的私密凭据，请勿上传或分享。

更新时停止旧版，将新版解压到新目录，运行新版的启动文件；新版会沿用同一用户数据目录。模型包编号没变时不必重新下载；编号变了，启动窗口会提示下载新的模型包。不要同时运行两个版本。不要删除用户数据目录，也不要直接用旧版程序打开新版修改后的数据库。

便携版不会自动导入开发目录的 `data/`。若要搬入已有资料，请先停止开发版和便携版、备份双方数据，再把开发目录下完整的 `data` 文件夹复制到 `%LOCALAPPDATA%\Zhiye\data`；不要合并两套数据库。浏览器本地保存的临时草稿不会随数据库迁移。

## 发布范围

发布包不包含作者的 `.env`、课程文件、数据库、API Key、OAuth 凭据或个人模型配置。公开模型和第三方组件按各自许可证提供；许可文件位于 `third-party/`、Python 包的 `.dist-info/`、Node 包和 Java 的 `legal/` 目录。

便携版标记为预发布，尚未在干净的 Windows 环境做系统验收。启动、解析或升级遇到问题时，请附上 `%LOCALAPPDATA%\Zhiye\logs` 中的日志提交 Issue。

## 开发者重新打包

在源码目录继续开发，安装依赖使用现有 `scripts/setup.ps1`。打包前准备可再分发的 Temurin 完整 JDK（脚本用 jlink 只打包 Tika 用到的模块，并预编译 DOC 辅助类），以及 `.cache/packaging/notices` 下许可声明。四个公开公式模型默认取自 `data/ocr-models/formula`，可用 `--formula-dir` 指定其他目录。向量模型使用项目固定的 Granite 快照，文字模型位于已安装 RapidOCR 的 `models` 目录。

```powershell
.\.venv\Scripts\python.exe .\scripts\build-portable.py --version 0.2.0-portable.1 --java-home "可再分发的Temurin-JDK目录"
```

脚本只编译前端、收集依赖和资源、生成主程序与模型包两个 ZIP 及各自的 SHA-256 文件，不运行测试或启动验证。模型包按 `MODELS_ID` 命名，`dist/` 中已有同名模型包时直接复用；更换向量模型版本或公式模型时，要同时修改 `build-portable.py` 的 `MODELS_ID` 和 `portable-start.ps1` 的 `$modelsId`。发布产物在 `dist/`，不进入 Git 版本控制。已有同名版本目录会阻止覆盖，请为下一次打包指定新版本号。
