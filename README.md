# 智鉴云 Zhijian Cloud

面向工程变更与签证索赔的图纸工作台。通过确定性 DXF 比对引擎与 Agent 工作流，连接图纸差异、人工复核、合同清单计价、签证草稿和变更台账。

## 功能

- 工具模式：上传新旧 DXF，识别新增、删除、修改和移动，复核变更并生成 DOCX 签证草稿。
- Agent 模式：按项目和图号维护版本链、差异报告、台账与归档。
- 以现场使用版本为比对基准；切换现场版本不改写历史台账。
- 支持合同清单导入，保留材料、厚度、单价与无法直接换算工程量的人工确认。
- 默认规则规划器可离线运行；可选接入 OpenAI 兼容接口完成工具规划。

## 本地运行

Python 3.11+。在仓库根目录运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe demo_app.py
```

浏览器打开 http://127.0.0.1:8321/ ，Agent 模式为 http://127.0.0.1:8321/agent 。Windows 也可在安装依赖后双击“启动工具模式.bat”。

其他系统使用 `python3 -m venv .venv`，再通过 `.venv/bin/python` 安装依赖、启动服务。

## 演示与数据

公开包仅包含由 `core_src/CAD修改智能识别agent/make_samples.py` 生成的合成方块图纸，不包含真实工程图纸、客户合同或历史任务。选择 blocks 样例即可体验比对。也可上传自己有权使用的新旧 DXF 和合同 CSV。

`jobs/`、`projects/`、`.runtime/` 会在本地运行时创建，不应提交到仓库。删除这些目录会清除本机任务或项目记录。

## 可选模型配置

复制 `llm_config.json.example` 为 `llm_config.json`，填写接口、模型与 API Key。也可通过 `CAD_LLM_API_KEY` 提供密钥。未配置时使用内置规则规划器。不要提交真实密钥。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest test_agent_baseline -v
```

覆盖现场基准选取、台账删除与去重、清单继承边界、厚度保存与异常计价拦截等 8 项用例。

## 目录

```text
demo_app.py          FastAPI 服务与接口
agent_core.py        Agent 编排、版本、台账与归档
demo_page.html       工具模式界面
agent_page.html      Agent 模式界面
assets/              页面脚本、样式及品牌素材
core_src/            DXF 比对引擎、配置与合成样例
test_agent_baseline.py 业务回归测试
```

## 边界

当前为本地运行版本，未实现生产级身份认证和多租户隔离，请勿将服务直接暴露到公网。识别结果、风险提示与金额估算需要专业人员复核，不替代设计审查或正式合同签证结论。
