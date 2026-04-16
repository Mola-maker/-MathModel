# 任务：将 mathmodel 的 PDF 阅读器升级为 PaddleOCR 方案

## 背景

`E:/mathmodel/agents/pdf_agent.py` 原先使用 `pdfplumber` 提取 PDF 文字。
该方案对纯文字 PDF 有效，但对扫描件/图像型 PDF 会返回空内容并报错：
> "Extracted content is too short, check whether PDF is scanned images"

需要将 PDF 提取后端替换为运行在 Docker 容器内的 **PaddleOCR**，
使其能正确处理图像型竞赛题目 PDF。

---

## 已知环境参数（执行前请用命令核实）

| 参数 | 值 | 核实命令 |
|------|----|---------|
| 宿主机工作目录 | `E:/mathmodel` | — |
| 题目 PDF 目录 | `E:/mathmodel/questiontest/` | `ls E:/mathmodel/questiontest/` |
| PaddleOCR 容器镜像 | 包含 `paddleocr` 的镜像 | `docker ps --format "{{.Names}}\t{{.Image}}"` |
| 容器内 Python | python3 | — |
| 容器内已有库 | `pypdfium2`, `paddleocr==3.4.x`, `paddlepaddle-gpu`, `numpy`, `pillow` | `docker exec <容器名> pip list` |

---

## 任务步骤

### Step 1 — 找到容器

```bash
docker ps --format "{{.Names}}\t{{.Image}}"
```

找到镜像名包含 `paddleocr` 的那一行，记录容器名（下文称 `<容器名>`）。

---

### Step 2 — 确认关键库版本

```bash
docker exec <容器名> bash -c "python3 -c 'import paddleocr; print(paddleocr.__version__)'"
docker exec <容器名> bash -c "python3 -c 'import pypdfium2'"
```

---

### Step 3 — 改写 `E:/mathmodel/agents/pdf_agent.py`

**核心改动：** 将原 `pdfplumber` 提取逻辑替换为以下流程：

```
PDF (宿主机)
  → docker cp 到容器 /tmp/
  → 容器内 pypdfium2 渲染每页为 RGB numpy 数组 (200 DPI)
  → PaddleOCR.predict(img) 识别文字
  → JSON 输出 [{page, lines:[{text, score}]}]
  → 宿主机解析 JSON → 拼接 raw_text
  → LLM (call_model) 格式化为 Markdown
  → 写入 translation/<stem>.md
```

**PaddleOCR 3.x 使用规范（已验证，不要改动）：**

```python
# 初始化 —— 必须加这三个 False，否则下载额外模型并因 GPU 架构报错
ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang="ch",
    device="cpu",           # GPU 架构 120 与 paddle 编译目标不兼容，强制 CPU
)

# 推理 —— predict() 返回 list[OCRResult]，OCRResult 是 dict-like
result = ocr.predict(img_array)   # img_array: np.ndarray shape (H,W,3) RGB
for item in result:
    texts  = item.get("rec_texts", [])   # 注意是 rec_texts（带 s）
    scores = item.get("rec_scores", [])  # 注意是 rec_scores（带 s）
```

**环境变量（容器内必须设置）：**

```bash
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
```

否则每次启动都会等待远程模型源连通性检测（超时缓慢）。

**容器发现逻辑（`_find_paddleocr_container`）：**

```python
def _find_paddleocr_container() -> str:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        name, image = line.split("\t", 1)
        if "paddleocr" in image.lower():
            return name.strip()
    raise RuntimeError("No paddleocr-vl container found")
```

**JSON 输出解析注意事项：**
容器 stdout 包含 paddle 的日志行（彩色 ANSI），JSON 数组在最后一行，
用以下方式提取：

```python
for line in stdout.splitlines():
    if line.strip().startswith("["):
        return json.loads(line.strip())
```

---

### Step 4 — 在容器内验证脚本可运行

将题目 PDF 复制到容器并执行：

```bash
docker cp "E:/mathmodel/questiontest/<题目>.pdf" <容器名>:/tmp/_ocr_input.pdf
docker exec <容器名> bash -c \
  "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
   python3 /tmp/_paddleocr_extract.py /tmp/_ocr_input.pdf 2>&1 | tail -5"
```

成功标志：stdout 最后一行是 `[{"page": 1, "lines": [...]}]`，`lines` 非空。

---

### Step 5 — 运行 main.py 端到端验证

```bash
cd E:/mathmodel && python main.py
```

观察日志：
- `[PDF-Agent] Extracting (PaddleOCR): xxx.pdf` ✓
- `[PDF-Agent] Recognized N text lines across M page(s)` ✓ (N > 0)
- `[PDF-Agent] Done → translation/xxx.md` ✓

---

## 常见错误与修复

| 错误信息 | 原因 | 修复 |
|---------|------|------|
| `Unsupported GPU architecture` | paddle 编译目标不含当前 GPU | 加 `device="cpu"` |
| `No such file: inference.yml` | 模型文件下载不完整 | 禁用 `use_textline_orientation=False` |
| `rec_texts` 为空列表 | 键名拼写错误用了 `rec_text` | 改为 `rec_texts` / `rec_scores` |
| `Cannot find JSON in OCR output` | stdout 中 JSON 被日志淹没 | 遍历行找 `startswith("[")` 的行 |
| `docker cp` 路径含中文失败 | Windows docker cp 不支持非 ASCII | 先在本地复制成 ASCII 文件名再 cp |

---

## 文件修改范围

只需修改一个文件：

```
E:/mathmodel/agents/pdf_agent.py   ← 全部替换
```

其余文件（`main.py`, `orchestrator.py`, `utils.py`）无需改动。
`PdfAgent` 类的公共接口保持不变（`convert_pdf_to_markdown` / `run`）。
