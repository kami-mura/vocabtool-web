# Contributing

感谢参与 VocabTool。请先通过 Issue 说明较大的功能或行为变化，再提交 Pull Request。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

提交前运行：

```bash
python -m pytest tests -q
ruff check app tests
python -m compileall -q app tests
node --check app/static/landing-v51.js
node --check app/static/auth.js
node --check app/static/skin.js
node --check app/static/ui-polish.js
```

## 提交规范

- 不提交 `.env`、数据库、备份、TTS 缓存或任何用户数据。
- 保持 Pull Request 聚焦，说明行为变化和验证方式。
- 涉及复习调度或 AI prompt 的改动，请先说明理由、影响和测试方案。
- 新增第三方依赖或数据时，请同时补充许可和来源说明。
