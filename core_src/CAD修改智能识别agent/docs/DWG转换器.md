# DWG → DXF 转换器（两条路径）

> 本引擎的矢量解析基于 DXF（ezdxf）。DWG 是 Autodesk 的二进制专有格式，
> 无法直接解析，因此**必须先转成 DXF**。引擎内置两条转换路径，可单独用、
> 也可互为回退。

---

## 1. 两条路径总览

```
                        ┌──────────────────────────┐
                        │        图纸.dwg          │
                        │     （二进制专有格式）     │
                        └────────────┬─────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
        ┌───────────────────────┐         ┌───────────────────────┐
        │  LibreDWG             │         │  ODA File Converter   │
        │  命令：dwg2dxf        │         │  命令：ODAFileConverter│
        │  开源免费             │         │  免费但不开源         │
        │  conda / apt / 源码   │         │  官网注册后下载        │
        │  快                   │         │  兼容性最强           │
        └───────────┬───────────┘         └───────────┬───────────┘
                    │  失败时互为回退 ↕                 │
                    ▼                                 ▼
            *_converted.dxf                     *_oda.dxf
            （缓存，互不覆盖）                   （缓存，互不覆盖）
                    └────────────────┬────────────────┘
                                     ▼
                        ┌──────────────────────────┐
                        │  ezdxf 解析 → 矢量比对    │
                        └──────────────────────────┘
```

| | LibreDWG (`dwg2dxf`) | ODA File Converter |
|---|---|---|
| **获取方式** | conda / apt / 编译源码 | 官网注册后下载（apt/pip 均无） |
| **授权** | 开源免费 | 免费但不开源 |
| **兼容性** | 普通 DWG 够用；高版本 DWG、天正图纸可能报错 | **最强**：官方 ODA 平台，高版本 DWG / 天正 / 代理实体 |
| **速度** | 快（实测 2.3s） | 中等（实测 3.3s） |
| **部署注意** | 加入 `PATH` 即可 | Qt6 图形程序，**无头环境必须 xvfb** |
| **缓存文件名** | `<原名>_converted.dxf` | `<原名>_oda.dxf` |

---

## 2. 选择用哪条：环境变量 `CAD_DIFF_CONVERTER`

三档取值，实测走向如下（以 `samples/active_shaft3/old.dwg` 验证，通过生成的
缓存文件名反查实际走了哪条路）：

| 设置 | 尝试顺序 | 实测实际使用 | 结果 |
|---|---|---|---|
| `auto`（**默认**） | LibreDWG → ODA | `old_converted.dxf` | 删除 10 处 |
| `dwg2dxf` | 只用 LibreDWG | `old_converted.dxf` | 删除 10 处 |
| `oda` | ODA → LibreDWG | `old_oda.dxf` | 删除 10 处 |

设置方式：

```bash
# Linux / macOS
export CAD_DIFF_CONVERTER=oda

# Windows PowerShell
$env:CAD_DIFF_CONVERTER="oda"

# Web 界面（启动脚本已支持透传）
CAD_DIFF_CONVERTER=oda bash web.sh
```

---

## 3. 安装要点

### 3.1 LibreDWG / dwg2dxf（默认，建议先装）

```bash
# 方式 A（推荐，conda）
conda create -n dwgconv -c conda-forge libredwg

# 方式 B（系统包管理器）
apt-get install libredwg-tools        # Debian/Ubuntu
brew install libredwg                 # macOS

# 验证
dwg2dxf --version
```

> **注意**：libredwg 对高版本/天正 DWG 常在 stderr 输出 ERROR 并以非零码退出，
> 但仍会写出可用的 DXF。引擎已做容错——**只要产物非空即视为转换成功**，
> 不会被它的退出码误导。

### 3.2 ODA File Converter（可选，兼容性最强）

**获取**：打开 <https://www.opendesign.com/guestfiles/oda_file_Converter>，
注册/登录 ODA 账号（免费）后下载。Linux 选 DEB 包，文件名形如
`ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb`。

**安装实测要点（Ubuntu 22.04）**：

```bash
# 1) 安装
sudo apt install ./ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb

# 2) 补 Qt6/xcb 运行库（不装会以退出码 134 崩溃）
sudo apt-get install -y \
  libxkbcommon-x11-0 libxkbcommon0 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-sync1 \
  libxcb-xfixes0 libxcb-render0 libxcb-glx0 libxcb-shm0

# 3) libxcb-util.so.0 软链（包名 libxcb-util0 已废弃）
sudo apt-get install -y libxcb-util1
cd /usr/lib/x86_64-linux-gnu
sudo ln -s libxcb-util.so.1 libxcb-util.so.0

# 4) 无头服务器必装 Xvfb
sudo apt-get install -y xvfb

# 5) 验证
which ODAFileConverter        # 应输出 /usr/bin/ODAFileConverter
```

**命令行用法**（引擎已封装，无需手动执行）：

```bash
ODAFileConverter <输入目录> <输出目录> <版本> <类型> <递归> <审计> [过滤文件名]
# 例
ODAFileConverter ./in ./out ACAD2018 DXF 0 1 xxx.dwg
```

### 3.3 无头运行：ODA 最大的坑

`ODAFileConverter` 是 Qt6 **图形程序**，即使纯命令行调用也需要 X 环境。
没有 X 服务时它会以退出码 134 崩溃：

```
qt.qpa.xcb: could not connect to display
Could not load the Qt platform plugin "xcb"
```

解法是套一层虚拟显示：

```bash
xvfb-run -a --server-args="-screen 0 1024x768x24" \
         ODAFileConverter ./in ./out ACAD2018 DXF 0 1 xxx.dwg
```

**引擎已内置自动兜底，你无需手动处理**：

- 不只看 `DISPLAY` 环境变量——服务器/Docker 里它常残留成 `:0` 但其实没有 X 服务，
  单看它会误判。改为检查 `/tmp/.X11-unix` 下是否存在 socket；
- 判定无 X 服务时，自动用 `xvfb-run` 包裹执行；
- 若有 X 服务但直连仍失败，会再补一次 xvfb 重试。

> 唯一前提：系统装了 `xvfb`。没装的话 ODA 路径会失败，引擎自动回退到
> dwg2dxf，不会中断流程。

---

## 4. 缓存机制（切换转换器时必读）

两条路径的产物**分别缓存、互不覆盖**：

```
old.dwg  →  old_converted.dxf    （LibreDWG 产物）
old.dwg  →  old_oda.dxf          （ODA 产物）
```

同名文件已存在且非空时**直接复用**，不会重复转换（对大图提速明显）。

> ⚠ **因此切换转换器后，若想强制重转，必须删掉对应的 `*_oda.dxf` /
> `*_converted.dxf`**，否则会一直用旧缓存，看起来"换了设置却没生效"。

---

## 5. 实测结论：标注测量值与转换器无关

这是最容易误解的一点，**换 ODA 并不能让标注测量值出现**。

### 5.1 两种转换器产出完全一致

同一张含 26 条 DIMENSION 的轴类图（`samples/active_shaft3/old.dwg`）：

| 转换器 | 实体总数 | LINE | MTEXT | DIMENSION | MULTILEADER | ARC | TOLERANCE |
|---|---|---|---|---|---|---|---|
| LibreDWG | 278 | 178 | 37 | 26 | 18 | 6 | 3 |
| ODA 27.1 | 278 | 178 | 37 | 26 | 18 | 6 | 3 |

**实体构成完全一致，差异为 0。** 端到端比对结果也一致（均检出删除 10 处，明细逐行 diff 无差异）。

### 5.2 根因：`<>` 是运行时占位符

DXF 里标注文字通常写成 `%%C<>`，其中 `<>` 是 AutoCAD 的**"运行时测量"占位符**，
真实数值根本不落盘。26 条标注里只有 9 条的 `text` 字段有内容，且全是 `%%C<>`
这类占位符；另 17 条 `text` 干脆是空字符串。两种转换器表现一致。

### 5.3 解法：解析层自行重算

引擎**不依赖转换器**，改用 ezdxf 的 `get_measurement()` 依标注定义点实时重算：

| 转换器 | 标注数 | 成功重算 | 示例值 |
|---|---|---|---|
| LibreDWG | 26 | **26** | 6.0 / 8.0 / 10.0 / 15.0 / 15.5 / 16.0 |
| ODA 27.1 | 26 | **26** | 6.0 / 8.0 / 10.0 / 15.0 / 15.5 / 16.0 |

报告中显示为 `尺寸标注 测量值=189.0` 这类形式。该值已纳入比对指纹——
**尺寸从 189 改成 200 会被判为 MODIFIED**，而不只是位置变化。

### 5.4 多重引线文字：确实无解

`MULTILEADER` 的文字存在**二进制 context data** 中，两种转换器都无法解出
（实测 18 条引线，`get_mtext_content()` / `context.mtext.default_content` /
`virtual_entities()` 三种途径全部为空）。

遇到时报告如实写「多重引线 (文字存于二进制context, 不可读)」，而不是瞎猜一个值。

---

## 6. 选型建议

| 场景 | 建议 |
|---|---|
| 日常使用 | 默认 `auto`（先 LibreDWG，快） |
| 高版本 DWG / 天正图纸转换异常 | 切 `oda`，两次结果对照看 |
| 服务器无图形环境 | 用 `auto`，并确保装了 `xvfb` |
| 想拿标注测量值 | **换转换器没用**——测量值由解析层 `get_measurement()` 重算，两条路径结果一致 |
