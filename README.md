# App User Voice

把分散的 App 评论与用户反馈整理成可追溯的周报，面向需要定期整理用户声音的产品、运营和开发团队。

这个项目来自一套持续使用的工作流：每周收集应用商店评论、外部反馈和客服记录，核对时间范围，整理重复内容，再把问题和对应原声写进报告。公开版本把原先固定的 App、文件和输出位置改为配置，让其他人也能运行和调整。

## 能完成什么

- **多来源整理**：导入 CSV、TSV、Excel，检查周期、评分和缺失字段，合并并精确去重。
- **问题分析**：按可配置规则分类、聚合问题，统计有效评分，保留未分类反馈。
- **原声追溯**：选取输入中的代表性原声，回到文件、Sheet 和行号核对，重复反馈保留全部出处。
- **本地周报**：生成 HTML、Markdown 和 Excel 明细，同时留下分析、质量检查和运行记录。

## 从数据到周报

```mermaid
flowchart LR
    A["文件导入<br/>可选七麦采集"] --> B["周期检查 · 清洗 · 去重"]
    B --> C["规则分类 · 问题聚合<br/>代表原声 · 出处核对"]
    C --> D["HTML · Markdown<br/>Excel 明细 · 审计 JSON"]
```

## 报告预览

下面是仓库合成数据实际生成的 HTML 报告：先看反馈与评分概览，再看问题和代表原声。

![合成数据生成的报告概览、问题统计和代表原声](docs/assets/report-overview.png)

示例四来源共 31 行，去掉 2 条精确重复和 4 条周期外记录后保留 25 条，形成 6 个问题；其中 1 条缺日期，会明确告警。有效商店评分 12 条，差评 10 条，差评率 83.3%。这些数字只说明 Demo 的处理结果。

[查看原声与出处核对示例](docs/USAGE.md#读懂报告)，或按下面的命令生成完整报告。所有示例反馈都是合成数据。

## 快速运行 Demo

需要 Python 3.10 或更新版本。下载仓库后，在项目根目录运行：

```bash
git clone https://github.com/ask6688/app-user-voice.git
cd app-user-voice
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m feedback_pipeline --config config.example.yaml
```

Windows PowerShell 用 `.venv\Scripts\Activate.ps1` 激活环境；命令提示符用 `.venv\Scripts\activate.bat`。其余步骤相同。

本地流程不需要 Node、钉钉或大模型账号。命令会打印本次输出目录 `outputs/<起止日期>/<本次运行时间>/`：

| 文件 | 用途 |
| --- | --- |
| `report.html` / `report.md` | 阅读与分享周报 |
| `details.xlsx` | 统计、问题、全量反馈、出处、排除记录与告警 |
| `analysis.json` / `quality.json` / `run.json` | 核对分析结果、输入版本、质量检查与完成状态 |

每次运行生成独立目录，不覆盖之前的报告，不修改输入文件。

## 换成自己的 App 和数据

```bash
cp config.example.yaml config.local.yaml
# 编辑 config.local.yaml，再运行：
python -m feedback_pipeline --config config.local.yaml --dry-run
python -m feedback_pipeline --config config.local.yaml
```

主要改五处：`app.name`、`period`、`sources.files`、`analysis.rules_file` 和 `output`。**请把示例来源全部替换为自己的文件**，建议放在已忽略的 `data/input/` 目录；路径相对于配置文件所在目录。

常见中文表头会自动识别，其他表头用 `columns` 映射。换 App 和分析规则不需要修改 Python。`--dry-run` 检查配置并打印计划，不读取输入数据或启动浏览器。

[配置、字段映射和统计口径](docs/USAGE.md) · [字段映射示例](config/mapping.example.yaml) · [通用规则](config/rules.generic.yaml)

## 两种分析方式

| 模式 | 适用场景 | 当前做法 |
| --- | --- | --- |
| `generic`（默认） | 根据自己产品的反馈主题调整规则 | YAML 定义类别、关键词、必要条件与排除条件，可多标签匹配 |
| `media`（可选） | 媒体与下载类产品 | 保留播放、浏览与搜索、会员、下载、新版本五模块的片段路由、相似度聚合和场景核对 |

两种方式都选取输入中的原声，不调用大模型。`media` 的一级模块固定，配置可调整评分、引文、告警阈值及场景核对开关。规则没有覆盖的反馈仍会保留，便于复核和继续完善规则。

```bash
python -m feedback_pipeline --config config/media.example.yaml
```

## 可选七麦采集

七麦模块使用专用浏览器登录并导出评论，保留日期回读、下载隔离和文件完整性检查。只有开启采集时才需要 Node.js 20+、Playwright 和平台账户；[安装与配置说明](docs/USAGE.md#可选七麦采集)。

**状态：已通过本地模拟回归，公开版尚未完成新的真实线上采集验收。** 文件导入与本地报告流程已实际跑通。七麦采集失败时，不会继续生成标记为完整的周报。

## 当前边界与后续

当前一次报告对应一个 App，输入文件应属于同一对象；没有定时调度或跨周期趋势。规则分析需要结合自己的数据调整，固定媒体专项规则也不适合所有 App。

默认遮盖正文中的手机号和邮箱形式内容，不输出原帖 URL；姓名和上下文身份仍需人工复核。精确去重在缺少评论 ID 时可能合并同渠道、同日的相同内容，详细口径见 [使用说明](docs/USAGE.md#统计与质量口径)。

后续按实际需要加入 DOCX、PDF、钉钉输入／发布／通知、多 App 和趋势分析。Web 界面暂不作为第一版目标。当前没有钉钉集成，也没有 AI 分析。

## 开发与公开范围

```bash
python -m unittest discover -s tests -v
python scripts/check_public_candidate.py .
```

运行数据、报告、登录状态和本地配置不属于公开文件；明确提交范围见 [PUBLIC_FILES.txt](PUBLIC_FILES.txt)。项目采用 [MIT License](LICENSE)；[公开范围与迁移说明](docs/PUBLIC_CANDIDATE.md)。
