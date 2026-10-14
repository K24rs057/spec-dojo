# spec-dojo(仕様道場)

AIでアプリを作る初心者が、先輩役のAIに教わりながら、要件定義 → 構想設計 → 基本設計 → AI向け実装指示書を自分で書き上げるローカルWebアプリです。

**開発中です。** いまは構想設計の工程が終わったところです。

## ドキュメント

- [要件定義書 ver.1.01](docs/requirements-v1.01.xlsx)
- [構想設計書 ver.1.00](docs/concept-design-v1.00.xlsx)
- AI活用記録:[要件定義](docs/ai-log/01-requirements.md) / [構想設計](docs/ai-log/02-concept-design.md)

## 予定している構成

- FastAPI / SQLite / HTML + JavaScript / pytest
- AIは Claude Code のヘッドレスモード(`claude -p`)を使います(追加料金なし)
