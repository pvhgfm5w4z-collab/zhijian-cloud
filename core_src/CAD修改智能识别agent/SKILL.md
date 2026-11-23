---
name: cad-change-detector
description: 识别同一张 CAD 图纸前后两版（DWG/DXF）的修改部位，自动圈注高亮修改处并输出结构化变更清单 + 多格式报告（含 LLM 中文摘要、PDF/HTML、栅格兜底）。支持机械零件图、已展开的天正暖通/给排水/电气图。当用户有两版 CAD 图纸想知道"改了哪里"时使用。
---

# CAD 图纸修改识别（cad-change-detector）

## 何时使用

- 用户提供**同一张图纸的旧版 + 新版**（DWG 或 DXF），想知道**改了哪些部位**，并希望**自动圈出/高亮修改处**。
- 典型场景：图纸版本比对、变更审查、竣工图与施工图差异核对。
- 支持：普通 CAD 图、**已展开的天正暖通/给排水/电气图**（如 `_t5` 格式，实测冷热源平面图可识别）。

## 快速用法

```bash
PY=/root/.pyenv/versions/3.11.1/bin/python3.11
DWG=/root/miniconda/envs/dwgconv/bin/dwg2dxf

# 1) DWG 先转 DXF（沙箱内 .dwg 二进制读不了；若已是 DXF 可跳过）
$DWG -o old.dxf /path/to/旧版.dwg
$DWG -o new.dxf /path/to/新版.dwg

# 2) 跑 diff（cad_diff_cli.py 在仓库根目录，内部转发到 src/cad_diff 引擎）
$PY cad_diff_cli.py old.dxf new.dxf -o result          # 兼容旧命令
# 或（功能更全，可指定配置）
$PY -m cad_diff.cli old.dxf new.dxf -o result -c configs/default.yaml
```

控制台打印 `新增 N | 删除 N | 修改 N | 移动 N`。

## 工作流程（管线）

1. **转换**：DWG → DXF（`dwg2dxf`，LibreDWG；ODA 回退）。沙箱/本机都需这一步。
2. **清洗**：`_clean_tmp` 修复 LibreDWG 导出 DXF 的格式瑕疵（见「关键踩坑」）。
3. **解析**（`parser.py`）：ezdxf 提取实体 → 记录表（type/layer/bbox/geometry/summary）。
   支持 **INSERT 块内实体递归展开**、**HATCH 填充边界**、PROXY_ENTITY 探测。
4. **配准**（`align.py`）：几何指纹哈希分桶 + 中位数估计全局平移；`rigid` 模式额外用
   线角度直方图估计整体旋转。
5. **Diff**（`diff.py`）：RTree 空间索引候选匹配 → 几何+位置评分 → 贪心一对一。
6. **分类**：按位移/几何阈值判 ADDED / DELETED / MODIFIED / MOVED。支持图层过滤。
7. **高亮**（`highlight.py`）：CHANGES 图层按类型画贴合形状（LINE→旋转细框、CIRCLE→圆、
   ARC→弧、DIMENSION/MTEXT→收紧矩形，自动剔除尺寸延伸线），颜色编码。
8. **渲染**（`render.py`）：整图对比 + 分簇聚焦放大图。
9. **报告**（`report.py`）：TXT 说明 + JSON 清单 + **PDF/HTML** 报告。
10. **语义**（`llm.py`）：规则式中文摘要（默认离线）或 OpenAI 兼容接口（失败回退规则）。
11. **栅格兜底**（`raster.py`，可选 `--raster`）：两版渲染成灰度图 → absdiff → 轮廓差异框。

## 模型配置（configs/）

所有行为旋钮集中在 `configs/default.yaml`（天正图用 `configs/tianzheng.yaml`）：
阈值(pos_tol/pos_eps/geom_eps)、配准(method)、图层过滤(mode/focus)、块展开(blocks.expand)、
实体类型开关(含 HATCH)、配色、输出格式、LLM(provider/key)。

## 输入要求

- **必须 DXF**：DWG 是二进制专有格式，需先用 `dwg2dxf` 转换。**只改扩展名无效**。
- **同一坐标系**：两版应来自同一图纸（引擎自动配准整体平移/旋转；纯缩放需先统一比例）。
- **天正图纸**：需已展开为标准图元（见「兼容性」）。

## 输出文件

| 文件 | 说明 |
|------|------|
| `result.dxf` | 新版图纸 + CHANGES 图层圈注（绿=新增/红=删除/橙=修改/品红=移动），CAD 可看 |
| `result.png` | 整图左右对比（旧/新） |
| `result_zoom*.png` | 分簇聚焦放大图：相近变更聚簇、每簇单独放大（大图必备） |
| `result.txt` | 变更文字说明：各类数量、每处坐标、删除项「原本内容」 |
| `result.json` | 结构化变更清单（含 meta + 语义摘要，可接 LLM 做报告） |
| `result.pdf` | PDF 报告：摘要 + 统计 + 明细表 + 对比图/聚焦图 |
| `result.html` | HTML 报告：自包含网页（内嵌图 + 明细表 + 摘要） |
| `result_raster.png` | （`--raster`）栅格级像素 diff 兜底图 |

## 兼容性（实测分级）

| 图纸类型 | 能否识别 | 说明 |
|---------|---------|------|
| 普通 CAD 图元（线/圆/弧/文字/原生标注/普通块） | ✅ | 完整支持 |
| 已展开的天正对象（`_t5`，转 DXF 后为标准图元） | ✅ | 实测暖通图可识别，含专业图层（如 `EQ-水地源热泵`） |
| 真·天正自定义对象（`PROXY_ENTITY`，代理图形缺失） | ⚠️ 可能漏检 | 需在装天正的 AutoCAD 里「图形导出/另存 T3」先展开 |

**判断**：转换后统计实体类型，若全是标准图元（无 PROXY_ENTITY）即可识别。

## 关键踩坑（LibreDWG 转换，引擎已自动修复）

- **XDATA 空行**：天正 XDATA 转换时混入空行，破坏 DXF 配对 → ezdxf 报 `Invalid group code`。
  引擎用**状态机清洗**：只删「组码位置」空行、保留「值位置」空行。
- **MATERIAL 表损坏**：保存时 ezdxf 崩溃 → 引擎按 `ENDTAB` 移除 MATERIAL 表，并用
  「新建干净文档+复制几何」双保险。

## 性能

- 全局对齐已优化为**几何指纹哈希分桶** O(n+m)；渲染用 `LineCollection` 批量绘制。
- 实测 **1.2 万实体**暖通图约 **6 秒**（初版逐实体 matplotlib 绘制约 60 秒，优化 10×）。

## 可调参数（configs/default.yaml）

```yaml
tolerances: { pos_tol: 20.0, pos_eps: 2.0, geom_eps: 0.5 }
registration: { method: translation }   # 或 rigid（自动估计旋转）
blocks: { expand: true }                # 递归展开块内实体
entity_types: { HATCH: true }           # 填充纳入比对
```

## 已知局限

- **HATCH 删除**：现已纳入比对（之前漏检）；但 HATCH 仅比存在性/位置，不比图案内容。
- **DIMENSION 测量值为估算**：dwg2dxf 不导出，引擎用展开图元最长线段近似。
- **纯缩放变换**：会改变几何指纹，刚体配准对"仅旋转+平移"有效；含整体缩放请先统一比例。
- **MTEXT 格式码 / 引线文字**部分转换时丢失，已 best-effort 提取。

## 依赖

| 组件 | 路径 / 安装 |
|------|------|
| 核心引擎 | `src/cad_diff/`（本目录） |
| DWG→DXF 转换器 | `/root/miniconda/envs/dwgconv/bin/dwg2dxf`（conda 装 `libredwg`，建议走清华镜像） |
| Python | `/root/.pyenv/versions/3.11.1/bin/python3.11` |
| Python 包 | `ezdxf`、`shapely`、`rtree`、`matplotlib`、`numpy`、`Pillow`、`PyYAML`、`opencv-python`、`scikit-image`、`reportlab`（见 `requirements.txt`） |

## 已验证

- 合成数据：增/删/改/移/平移/旋转配准/多图层。
- 真实机械图（主动轴）：DIMENSION/MTEXT/LINE/MULTILEADER 删除。
- 天正暖通图（冷热源 `_t5`，1.2 万实体）：含专业图层管线/设备/HATCH 删除；刚体配准可用。
- 拓展模块：块递归展开、HATCH 检测、刚体(旋转)配准、栅格兜底、PDF/HTML 报告、LLM 语义摘要均已实测通过（见 `docs/架构.md`）。
