# CAD 图纸修改智能识别 Agent

> 给定同一张 CAD 图纸的**旧版 + 新版**（DWG/DXF），自动找出**改了哪些部位**，
> 在图上**圈注高亮**，并产出**结构化清单 + 中文摘要 + 多格式报告**。

设计思路：确定性**矢量 diff（占 80% 精度）** + 可选 **LLM 语义层（占 20% 表达）**。
不靠多模态大模型"看图猜差异"（会漏改、会幻觉），而是先解析成结构化实体表，
用几何/空间算法精确比对，再让 LLM 做摘要。

---

## 1. 目录一览

| 位置 | 作用 | 你主要改/看这里 |
|------|------|----------------|
| `configs/` | **模型配置** | `default.yaml` / `tianzheng.yaml` —— 阈值、配准、图层、配色、输出、LLM 全在这 |
| `src/cad_diff/` | **引擎** | 分层实现：解析 / 配准 / 比对 / 高亮 / 渲染 / 报告 / 语义 |
| `outputs/` | **输出结果** | 按数据集归档；`outputs/latest/` 是示范运行（hvac 全格式） |
| `samples/` | 输入样例 | 旧版/新版图纸（blocks / real / active_shaft / hvac / cases 回归用例） |
| `docs/架构.md` | 架构细节 | 管线、分层职责、拓展模块清单、已知局限 |
| [`docs/DWG转换器.md`](docs/DWG转换器.md) | **转换器专题** | 两条 DWG→DXF 路径对比、优先级切换、无头部署、标注测量值实测结论 |
| `run_regression.py` | 回归测试 | 9 个用例一键验证（6 真实图纸 + 3 判定规则专项） |

---

## 2. 快速上手

```bash
PY=/root/.pyenv/versions/3.11.1/bin/python3.11
DWG=/root/miniconda/envs/dwgconv/bin/dwg2dxf

# DXF 直接跑（推荐）
$PY cad_diff_cli.py <旧版.dxf> <新版.dxf> -o outputs/myrun/result

# DWG 直传（需 dwg2dxf 在 PATH，否则引擎自动尝试转换）
export PATH=/root/miniconda/envs/dwgconv/bin:$PATH
$PY cad_diff_cli.py <旧版.dwg> <新版.dwg> -o outputs/myrun/result

# 用配置文件（推荐，模型行为一目了然）
PYTHONPATH=src $PY -m cad_diff.cli <旧> <新> -o outputs/myrun/result -c configs/default.yaml
```

控制台打印：`新增 N | 删除 N | 修改 N | 移动 N`。

---

## 3. 模型配置（configs/*.yaml）

所有"旋钮"集中在 YAML，改完生效，无需动代码。节选：

```yaml
tolerances:          # 几何/位置容差（图纸单位，毫米）
  pos_tol: 20.0      #   空间候选匹配半径
  pos_eps: 2.0       #   位移超此值=移动
  geom_eps: 0.5      #   几何差异超此值=修改
registration:
  method: translation   # translation | rigid(自动估计旋转)
layers:
  mode: all             # all | only | except（only/except 支持 * 通配）
  focus: []
blocks:
  expand: true          # 递归展开块内实体（捕获块内部修改）
entity_types:
  HATCH: true           # 填充纳入比对（关闭"填充删除漏检"）
output:                 # 各输出格式开关
  dxf: true; png: true; zoom: true; txt: true
  json: true; pdf: true; html: true; raster: false
llm:
  provider: rule        # rule(离线规则摘要) | openai(兼容接口)
```

完整字段见 `configs/default.yaml` 注释。

---

## 4. 输出文件（每次运行产出）

以 `outputs/myrun/result` 为前缀：

| 文件 | 说明 |
|------|------|
| `result.dxf` | 新版图纸 + **CHANGES** 图层圈注（绿=新增/红=删除/橙=修改/品红=移动），AutoCAD/中望/FreeCAD 可看 |
| `result.png` | 整图左右对比（旧/新） |
| `result_zoom*.png` | 分簇聚焦放大图：相近变更聚簇、每簇单独放大（大图必备） |
| `result.txt` | 文字说明：各类数量、每处坐标、**删除项"原本内容"** |
| `result.json` | 结构化变更清单（含 meta + 语义摘要），可接 LLM/下游 |
| `result.pdf` | PDF 报告：摘要 + 统计 + 明细表 + 对比图/聚焦图 |
| `result.html` | HTML 报告：自包含网页（内嵌图 + 明细表 + 摘要） |
| `result.docx` | **Word 报告**：与 PDF 同结构（摘要 + 统计 + 明细表 + 对比图/聚焦图），中文已设东亚字体，Office/WPS 可直接编辑 |
| `result_raster.png` | （`--raster` 或 `output.raster=true` 时）栅格级像素 diff 兜底图 |
| **`result.zip`** | **上述全部产物的聚合包**（默认开启 `output.zip: true`），一键交付/下载 |

> 产物全部聚合成 `result.zip` 后，散落文件即可视为"中间态"，zip 才是交付物。

**Web 界面下的文件管理**：每次比对在 `outputs/web_uploads/job_<时间戳>/` 落盘并自动打包 `result.zip`。
可用界面右下「🗂️ 文件管理」面板查看历史任务、逐个下载 zip，或「清理旧任务」保留最近 5 个、删除其余。
对应接口：`GET /api/jobs`（列表）、`POST /api/clean?keep=5`（清理）。

---

## 5. 拓展能力（相对初版 MVP 全部补齐）

| 能力 | 怎么用 | 价值 |
|------|--------|------|
| **块内实体递归展开** | `blocks.expand: true` | 捕获块**内部**修改（之前只比对块引用位置） |
| **HATCH 填充检测** | `entity_types.HATCH: true` | 关闭"填充删除漏检" |
| **刚体配准（旋转+平移）** | `registration.method: rigid` | 新版整体旋转时仍能对齐 |
| **图层过滤** | `layers.mode/focus` | 只比关注图层，提精度/性能 |
| **LLM 语义摘要** | `llm.provider: openai` + key | 中文工程变更说明（无 key 自动用规则版） |
| **PDF / Word / HTML 报告** | 默认开启（`output.docx/pdf/html`） | 闭环交付：先生成 Word 再生成 PDF，中文均正常（PDF 用 STSong-Light CID 字体，不黑块） |
| **栅格级兜底 diff** | `--raster` | 矢量解析失败也有像素级差异 |
| **批量 / 目录模式** | `--batch pairs.csv` / `--dir` | 一次比对多对图纸 |
| **多转换器回退 + 天正探测** | 自动 | dwg2dxf/ODA 回退；探测 PROXY_ENTITY 并提示 |

---

## 6. 实测（outputs/ 已附结果）

| 样例 | 规模 | 结果 |
|------|------|------|
| `samples/blocks`（合成） | 9→11 | 新增3/删除1/修改1/移动1（4 类全中） |
| `samples/real`（真实方块） | 4→6 | 新增 2（中心线） |
| `samples/active_shaft`（主动轴） | 271→270 | 删除 1（DIMENSION） |
| `samples/active_shaft2/3`（主动轴） | 271→261 | 删除 10（DIMENSION/MTEXT/LINE/MLeader） |
| `samples/hvac`（天正暖通 _t5） | 1.24万→1.24万 | 删除 14（含 HATCH + 专业图层 `EQ-水地源热泵`，约 6 秒） |

### 变更是怎么判定的（看报告前先读这段）

四类变更不是靠"位置变没变"拍脑袋分的，核心是**先配对、再分类**：

| 判定 | 条件 | 典型场景 |
|------|------|----------|
| **未变化** | 配上了，且几何差 `< geom_eps` 且位移 `< pos_eps` | — |
| **MOVED 移动** | 几何几乎没变，但位置动了 | 整块图元被挪位 |
| **MODIFIED 修改** | 配上了，但几何属性变了 | 尺寸值 189→200、文字改写、字高变化、线长变化 |
| **DELETED / ADDED** | 配不上对 | 真删了 / 真加了 |

最容易踩坑的是 **"原地改内容" 与 "挪窝的增删" 的区分**，这两条规则互相拉扯：

- 若把"内容不同"直接判为**不可配对**，那么改一个字会报成「同坐标删一处 + 增一处」，
  用户看得一头雾水；
- 若把"内容不同"判为**完全不受罚**，那么"这里删一段、别处加一段"会被误报成"修改"。

本工具的取法是：内容改写施加固定罚值 `TEXT_EDIT_PENALTY=10`，
判据为 `中心距 + 2×几何差 < pos_tol×2`。取 10 即要求中心距 `< 20`，与 `pos_tol`
同量级——**原地改内容的判修改，挪了窝的各归增删**。两种情形都有回归用例守着
（`samples/cases/`）。

跑 `python3.11 run_regression.py` 可一键验证全部 9 个用例（含 DWG/ODA 双路径）。

---

## 6.5 Web 交互界面

除了命令行，还提供浏览器界面：拖拽上传旧版/新版图纸（DWG/DXF/ZIP），选配置预设，
点「比对」后在线看圈注图、聚焦图、变更清单与中文摘要，并一键下载各格式产物。

```bash
# 启动（默认 8000 端口，可加端口参数）
bash web.sh 8000
# 浏览器打开： http://localhost:8000
```

> 启动脚本会自动把 `src/` 加入 `PYTHONPATH`、把 dwg2dxf 加入 `PATH`，
> 并以前台常驻方式运行（Ctrl+C 停止）。

**界面能力**
- 上传：旧版 / 新版（支持 `.dxf`、`.dwg`、`.zip` 内嵌图纸）
- 预设：`default` / `tianzheng`；可勾选「栅格 diff」「关闭 LLM」
- 结果区：统计卡 + 整图/聚焦图轮播 + 变更明细表 + 中文摘要 + 7 类产物下载

**HTTP 接口（可对接第三方 / 自动化）**
| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/`                | 前端单页 |
| `GET`  | `/api/presets`     | 列出可用预设 |
| `POST` | `/api/compare`     | `old`/`new` 文件 + `preset`/`registration`/`raster`/`no_llm` 表单 → JSON（含 base64 图、清单、下载链接，产物已打包 `result.zip`） |
| `GET`  | `/api/file?job=&name=` | 下载某次比对的产物（仅限本 job 目录，防目录穿越） |
| `GET`  | `/api/jobs`        | 列出历史比对任务（文件数/大小/时间/是否含 zip） |
| `POST` | `/api/clean?keep=5`| 清理旧任务，仅保留最近 `keep` 个（默认 5） |

---

## 7. 已知局限（诚实标注）

- **真·天正 PROXY_ENTITY**：代理图形缺失仍可能漏检，需先在装天正的 AutoCAD 里"另存 T3 / 图形导出"展开。
- **MULTILEADER 文字**：存在二进制 context data 中，当前读不出，报告如实标注
  「多重引线 (文字存于二进制context, 不可读)」。**这与用哪个 DWG 转换器无关**——
  LibreDWG 与 ODA File Converter 实测表现一致。
- **纯缩放变换**：会改变图元几何指纹，刚体配准对"仅旋转+平移"有效；含整体缩放请先统一比例。
- **MTEXT 格式码**：已 best-effort 清洗（`\P` 换行、`%%c`→φ 等）。

> **关于 DIMENSION 测量值（已解决，不是局限）**：DXF 里的标注文字常写成 `%%C<>`，
> `<>` 只是"运行时测量"占位符，真实数值不落盘——所以换任何转换器都拿不到。
> 本工具改用 ezdxf `get_measurement()` 依标注定义点**实时重算**，实测 26 条标注
> 全部算出精确值（如 `测量值=189.0`），且该值参与比对：尺寸从 189 改成 200
> 会被判为 MODIFIED，而不只是位置变化。

> **关于 DIMENSION 测量值（已解决，不是局限）**：DXF 里的标注文字常写成 `%%C<>`，
> `<>` 只是"运行时测量"占位符，真实数值不落盘——所以换任何转换器都拿不到。
> 本工具改用 ezdxf `get_measurement()` 依标注定义点**实时重算**，实测 26 条标注
> 全部算出精确值（如 `测量值=189.0`），且该值参与比对：尺寸从 189 改成 200
> 会被判为 MODIFIED，而不只是位置变化。完整实测数据见
> [`docs/DWG转换器.md`](docs/DWG转换器.md#5-实测结论标注测量值与转换器无关)。

---

## 8. 相关文档

- [`docs/架构.md`](docs/架构.md) —— 架构细节：管线、分层职责、拓展模块清单、已知局限
- [`docs/DWG转换器.md`](docs/DWG转换器.md) —— DWG→DXF 两条转换路径详解
- [`docs/LLM摘要评测.md`](docs/LLM摘要评测.md) —— 语义摘要层评测：请求链路、清单瘦身、降级兜底、能力边界

---
