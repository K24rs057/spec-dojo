"""AIへの依頼文と、AIが返すJSONの形(基本設計書 5 AIとのやりとり)。

AIには「先輩の説明文 → 区切り行 → JSON」の順で書かせる。説明文だけを画面に流し、JSONは隠す。
"""
from typing import List

from pydantic import BaseModel, Field, field_validator

import rules

MARKER = "<<<SPEC_DOJO_JSON>>>"


# ---- AIが返すJSONの形 ----
class Option(BaseModel):
    id: str
    label: str
    advice: str


class Term(BaseModel):
    term: str
    explanation: str


class QuestionOut(BaseModel):
    question: str
    options: List[Option] = Field(min_length=2, max_length=5)
    hint: str
    recommended: List[str] = []
    terms: List[Term] = []


class AdviceOut(BaseModel):
    value: str
    reason: str = ""
    contradiction: str = ""


class Candidate(BaseModel):
    item_key: str
    value: str
    reason: str = ""

    @field_validator("item_key")
    @classmethod
    def known_item(cls, v):
        try:
            rules.item(v)
        except KeyError:
            raise ValueError(f"知らない項目キーです:{v}")
        return v


class ChatOut(BaseModel):
    candidates: List[Candidate] = []


class Finding(BaseModel):
    item_key: str
    viewpoint_id: str
    message: str


class ReviewOut(BaseModel):
    findings: List[Finding] = []


# ---- 依頼文 ----
COMMON = f"""あなたは「仕様道場」というツールの中の、先輩エンジニア役です。
相手は、AIでアプリを作りたい初心者です。相手が頭の中で思い描いているアプリを、要件定義書と設計書の形で言葉にする手伝いをします。

守ること:
- 正解を押しつけない。あなたの意見は「おすすめ」であって、正解ではない
- 専門用語はそのまま使ってよいが、初めて出す用語は terms に「ひとことで言うと」の説明を入れる
- ファイルを読んだり、コマンドを実行したり、ツールを使ったりしない。文章を書くだけ
- 出力の形を必ず守る:まず普通の日本語の文章を書き、次に {MARKER} だけの行を1行書き、そのあとにJSONだけを書く。JSONをコードブロックで囲まない
"""


def context(project, decisions, glossary):
    lines = [f"プロジェクト名:{project['name']}", "", "ここまでに決まっていること:"]
    any_decided = False
    for p in rules.phases():
        for it in p["items"]:
            d = decisions.get(it["key"])
            if d and d["status"] != "undecided":
                any_decided = True
                mark = "(要確認)" if d["status"] == "needs_check" else ""
                lines.append(f"- [{it['key']}] {p['label']} / {it['label']}{mark}:{d['value']}")
    if not any_decided:
        lines.append("- まだ何も決まっていない")
    if glossary:
        lines.append("")
        lines.append("すでに説明した用語(同じ用語は説明し直さない):" + "、".join(g["term"] for g in glossary))
    return "\n".join(lines)


def question_prompt(project, decisions, glossary, item_key):
    it = rules.item(item_key)
    phase = next(p for p in rules.phases() if p["key"] == rules.phase_of(item_key))
    return f"""{COMMON}
{context(project, decisions, glossary)}

今の工程:{phase['label']}
今決める項目:{it['label']}(この項目で決めること:{it['guide']})

やること:
1. 文章:今の工程と項目で何を決めるのか、なぜ決めるのかを、先輩として3〜4文で説明する
2. JSON:この項目を決めるための質問を1つ作る

JSONの形:
{{"question": "質問文", "options": [{{"id": "A", "label": "選択肢", "advice": "この選択肢を選んだ人へのアドバイス(2〜3文。良い点と気をつける点)"}}], "hint": "選べないときの考え方のヒント(答えは書かない)", "recommended": ["あなたのおすすめの選択肢のid"], "terms": [{{"term": "用語", "explanation": "ひとことで言うと"}}]}}

選択肢のルール:
- 3〜4個。A, B, C, D の順に id を付ける
- 選択肢どうしが含み合わないようにする。「Aに加えて〜」「Aと同じだが〜」のように、別の選択肢を含む書き方をしない(相手は複数選べるので、組み合わせは相手が選ぶ)
- このプロジェクトの内容に合わせて、具体的に書く
"""


def advice_prompt(project, decisions, glossary, item_key, question, selected, free_input):
    it = rules.item(item_key)
    chosen = [o for o in question["options"] if o["id"] in selected]
    chosen_text = "\n".join(f"- {o['id']}:{o['label']}" for o in chosen) or "- (選択肢は選んでいない)"
    return f"""{COMMON}
{context(project, decisions, glossary)}

今決める項目:{it['label']}
出した質問:{question['question']}
相手が選んだ選択肢:
{chosen_text}
相手が自由に書いたこと:{free_input or '(なし)'}
あなたのおすすめ:{', '.join(question.get('recommended', [])) or '(なし)'}

やること:
1. 文章:相手の答えへのアドバイスを3〜5文で書く。良い点と気をつける点を書く。選択肢を複数選んでいる場合は、同時に成り立つかを確かめる。おすすめと違っていても、その道で問題を防ぐ工夫を添える
2. JSON:決定事項として保存する内容を、相手の答えをもとに1〜2文でまとめる

JSONの形:
{{"value": "決定事項として保存する内容", "reason": "相手が書いた理由があればその要約。なければ空", "contradiction": "選んだものどうし、または決まっていることとの矛盾があれば1文で。なければ空"}}
"""


def chat_prompt(project, decisions, glossary, history, message, phase_key):
    keys = "\n".join(f"- {it['key']}:{it['label']}({it['guide']})" for it in rules.items(phase_key))
    talk = "\n".join(f"{'相手' if t['role'] == 'user' else 'あなた'}:{t['text']}" for t in history[-8:])
    return f"""{COMMON}
{context(project, decisions, glossary)}

今の工程の項目:
{keys}

これまでの会話:
{talk or '(なし)'}

相手の新しい発言:{message}

やること:
1. 文章:相手の発言に先輩として2〜4文で返事をする。決められそうなことがあれば「〇〇として抜き出しました」と書く
2. JSON:発言から決められそうな項目があれば候補にする。なければ空の配列。item_key は上の項目のキーだけを使う

JSONの形:
{{"candidates": [{{"item_key": "項目のキー", "value": "決定事項として保存する内容", "reason": "発言から読み取れた理由"}}]}}
"""


def review_prompt(project, decisions, glossary, target_keys):
    vps = "\n".join(f"- {v['id']}:{v['text']}" for v in rules.review_rules()["viewpoints"])
    targets = "\n".join(f"- {k}" for k in target_keys)
    return f"""{COMMON}
{context(project, decisions, glossary)}

レビューの観点:
{vps}

レビューする項目:
{targets}

やること:
1. 文章:全体の印象を2〜3文で書く
2. JSON:観点に沿った指摘を出す。指摘は本当に直す価値があるものだけにする(なければ空の配列)。1件ごとに観点の番号を必ず付ける。ほかの項目との矛盾は V-07 で指摘する

JSONの形:
{{"findings": [{{"item_key": "項目のキー", "viewpoint_id": "V-01", "message": "指摘の内容(何が問題で、どう直せばよいか)"}}]}}
"""
