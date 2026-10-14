# spec-dojo(仕様道場)

AIでアプリを作る初心者が、先輩役のAIに教わりながら、要件定義 → 構想設計 → 基本設計 → AI向け実装指示書を自分で書き上げるローカルWebアプリです。

作ったきっかけは、大学のプロジェクトベース設計演習で、要件定義や設計の進め方が分からずに苦戦したことです。目的は「頭の中で思い描いているアプリを言葉にして、AIに作らせたときに思い描いたとおりのものができるようにする」ことです。

## できること

- **ガイド付きモード**:先輩AIが1問ずつ、選択肢つきで質問します。選んだあとに「アドバイス」が出ます(おすすめは正解ではなく一つの意見です)
- **自由チャットモード**:思いついたことを書くと、AIが決められそうな項目を抜き出し、「この内容で決定しますか?」と聞きます
- **3段構えのレビュー**:①曖昧語辞書(AIなし)②観点リスト ③先輩AI。指摘には観点の番号が付きます
- **決定の変更**:前提を変えると、それに依存している項目に「⚠要確認」が付きます。変更の履歴はすべて残ります
- **資料の出力**:要件定義書・構想設計書・基本設計書をExcelとMarkdownで、実装指示書をMarkdownで出します。途中のものは「下書き ver.0.x」、工程を完了すると ver.1.00 になります

## 起動

`仕様道場を開く.bat` をダブルクリックすると、Chromeで画面が開きます。

コマンドで起動する場合:

```powershell
python app.py
```

必要なもの:Python 3.11、Claude Code(`claude` コマンド)とそのサブスク契約。パッケージは次で入ります。

```powershell
pip install -r requirements.txt
```

## AIについて

AIは Claude Code のヘッドレスモード(`claude -p`)で呼び出すので、サブスクの範囲で動き、追加の料金はかかりません。入力した内容はAnthropicに送られます。パスワードやAPIキーのような文字列は、送る前に警告して止めます。

AIを呼び出す部分は `llm.py` の `LLM` クラスにまとめてあり、ほかのAIにも差し替えられる作りです(今回は claude -p だけを実装しています)。

## しくみ

| ファイル | 役割 |
|---|---|
| `app.py` | エントリポイント。FastAPIのAPI(基本設計書のA1〜A15) |
| `store.py` | SQLiteへの保存。決定の更新と履歴の追加は1つのトランザクション |
| `jobs.py` | AIの仕事を裏で動かし、SSEで画面に流す。説明文とJSONを区切り行で分ける |
| `prompts.py` | AIへの依頼文と、AIが返すJSONの形(pydanticで検証、崩れていたら1回だけやり直す) |
| `llm.py` | AIを呼び出す層(claude -p) |
| `documents.py` | 資料の生成、版番号、Excel出力 |
| `rules.py` / `skeleton.yaml` / `review_rules.yaml` | 工程と項目の骨組み、曖昧語辞書、観点リスト |
| `static/` | 画面(HTML+素のJavaScript) |

## テスト

```powershell
python -m pytest -q
```

AIは偽物(`FakeLLM`)に置き換えてテストするので、claude -p は呼びません。GitHub Actions(Windows)で push のたびに動きます。

テストの項目と結果、画面での確認、見つけた不具合と残った課題は [テスト仕様書・結果報告書](docs/test-report-v1.00.xlsx) にまとめています。

## ドキュメント

- [要件定義書 ver.1.01](docs/requirements-v1.01.xlsx)
- [構想設計書 ver.1.00](docs/concept-design-v1.00.xlsx)
- [基本設計書 ver.1.00](docs/basic-design-v1.00.xlsx)
- [テスト仕様書・結果報告書 ver.1.00](docs/test-report-v1.00.xlsx)
- AI活用記録:[要件定義](docs/ai-log/01-requirements.md) / [構想設計](docs/ai-log/02-concept-design.md) / [基本設計](docs/ai-log/03-basic-design.md) / [実装](docs/ai-log/04-implementation.md) / [テスト](docs/ai-log/05-test.md)
