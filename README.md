# Transplate

Windows 实时字幕转简体中文：**区域截图 → OCR → 繁体转简体 / 其他语言翻译 → 无焦点字幕浮层**。

项目处于 **v0.1.0 首版**。不读取视频内部字幕轨，不修改源文件，也不是直接替换播放器字幕；它使用屏幕 OCR，适用于浏览器、常规桌面播放器和其他窗口化播放场景。

## 能力

- **离线繁体转简体**：OpenCC 转换，字幕文本不出本机，免费，无 API Key。
- **多语言字幕 OCR**：RapidOCR 默认中英识别模型随程序打包；繁体专用、日文、韩文、英文、拉丁字母语言模型首次使用时按需下载并缓存在 `%APPDATA%\Transplate\ocr-models`。请选对识别语言，不会自动识别所有语言。
- **翻译成简体**：可选 Google 翻译（网络，基于第三方非官方客户端）或 OpenAI Chat Completions 兼容服务（例如本机 Ollama 或你自己的 API 服务器）。API Key 仅保留在进程内存中，不存储到偏好文件。
- **覆盖字幕**：置顶、鼠标穿透的字幕浮层；支持 Windows 10 2004+ 的 `WDA_EXCLUDEFROMCAPTURE` 以防字幕被 OCR 重复读取。不支持时将浮层移到选定区域外。
- 选择字幕位置、调节 OCR 间隔和字号；翻译结果缓存、仅字幕变化时触发翻译；区域支持多显示器/不同 DPI 的坐标转换（混合缩放复杂布局仍建议验证）。

## Windows 直接运行

在 [Actions](../../actions/workflows/windows.yml) 打开最新 **Build Windows** 成功记录，在 **Artifacts** 下载 `Transplate-Windows-x64`，解压 ZIP，运行 `Transplate/Transplate.exe`。这是免安装目录版，**不要只单独复制 EXE**，旁边的 `_internal` 目录包含模型与动态库。

> GitHub Actions 构建文件在 workflow 成功后才可下载；仓库是私有的，需要已登录且有仓库访问权。当前没有发布 Release。

使用步骤：

1. 在播放器打开视频，建议使用窗口化/无边框窗口模式。
2. 点 **框选字幕区域**，在字幕出现的位置拖拽一个仅包含 1–2 行字幕的长方形；按 Esc 取消。
3. 选择 **繁体 → 简体** 或 **其他语言 → 简体**，并选择正确的 OCR 识别语言。
4. 翻译模式选择服务：Google（会上传识别字幕文本）或 OpenAI 兼容 API。若在 Windows 本机运行 Ollama 的 Chat Completions 兼容服务，可填 `http://127.0.0.1:11434/v1` 和已安装的模型名；需提前自行准备并启动模型服务。
5. 点击 **开始实时转换**。转换后的字幕覆盖原位置；若系统无法排除浮层录屏则显示在选区外。停止按钮可以立即终止下一轮抓屏（正在执行的 OCR / 网络请求可能需要完成后才退出）。

注意：**简繁转换 ≠ 跨语言翻译**。日文、韩文、英文等字幕必须选翻译模式。OCR 和网络翻译存在非零延迟、误读和翻译错误，不保证逐帧实时。DRM 保护、独占全屏、某些视频硬件叠加层可能返回黑屏或无法覆盖；此时换窗口播放模式。Google 翻译客户端非官方，可能受网络环境、访问限制或接口变更影响。

## 从源码运行

需要 Windows 10/11 x64、Python 3.11 或 3.12。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python launcher.py
```

首次打包已包含默认的中英 OCR 模型，不必联网进行简繁转换。更多 OCR 语种需要首次联网下载模型。

## 开发与构建

```powershell
python -m unittest discover -s tests -v
pip install pyinstaller
pyinstaller --noconfirm --clean --onedir --windowed --name Transplate --collect-all rapidocr --collect-all onnxruntime --collect-all cv2 --collect-all opencc --hidden-import=onnxruntime.capi.onnxruntime_pybind11_state launcher.py
.\dist\Transplate\Transplate.exe --self-test
```

自动构建定义在 `.github/workflows/windows.yml`，每次推送 `main`、PR 或手动触发均会运行 Windows 单测、PyInstaller 打包、离线默认 OCR 冒烟测试，并上传 Windows ZIP 构建产物。

## 架构 / Android 方向

```
transplate/core.py       OpenCC 与文本过滤（可复用核心逻辑）
transplate/ocr.py        RapidOCR 语言模型适配器
transplate/translator.py 翻译服务适配器
transplate/worker.py     后台截图/识别/翻译循环
transplate/app.py        Windows Qt 界面、选区与透明浮层
```

Android 后续可沿用 OCR/翻译业务逻辑设计，但需要另外实现 `MediaProjection` 屏幕捕获、Android 悬浮窗权限/UI，以及相应 OCR 推理引擎；**不能直接把 Windows EXE 转成 APK**。字幕合规性及视频平台使用规则由使用者自行确认。

## 隐私与限制

- 本地简繁转换不上传文字、图片；翻译模式下 Google 翻译会接收识别的字幕文字。OpenAI 兼容 API 由你指定服务位置，远程 API 同样会传输文字。
- 字幕截图只在内存中处理，不持久化；`%APPDATA%\Transplate\settings.json` 存偏好和区域坐标，不含 API Key；语言模型缓存位于同一目录下。
- 初版暂不提供全局快捷键、托盘菜单、原始字幕区域的智能遮挡、批量字幕导出或音频识别；这些是未来迭代方向。
- 项目源码采用 MIT 许可证；OCR 引擎及模型、其他依赖遵循各自许可证。
