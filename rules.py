"""骨組み(skeleton.yaml)とレビューの基準(review_rules.yaml)を読み込む。

どちらもコードを触らずに項目や観点を足せるように、データファイルに分けている。
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent


@lru_cache
def skeleton():
    return yaml.safe_load((BASE / "skeleton.yaml").read_text(encoding="utf-8"))


@lru_cache
def review_rules():
    return yaml.safe_load((BASE / "review_rules.yaml").read_text(encoding="utf-8"))


def phases():
    return skeleton()["phases"]


def phase_keys():
    return [p["key"] for p in phases()]


def phase_of(item_key):
    return item_key.split(".", 1)[0]


def items(phase_key=None):
    out = []
    for p in phases():
        if phase_key is None or p["key"] == phase_key:
            out.extend(p["items"])
    return out


def item(item_key):
    for it in items():
        if it["key"] == item_key:
            return it
    raise KeyError(item_key)


def dependents(item_key):
    """item_key を前提にしている項目のキー(直接の依存だけ)。"""
    return [it["key"] for it in items() if item_key in it.get("depends_on", [])]


def lint(text):
    """①曖昧語辞書と秘密情報のパターンを照合する。AIは使わない。"""
    rules = review_rules()
    warnings = []
    for word in rules["vague_words"]:
        if word in text:
            warnings.append({"kind": "vague", "word": word,
                             "message": f"「{word}」はあいまいな言葉です。数字や確かめられる条件にできますか?"})
    if contains_secret(text):
        warnings.append({"kind": "secret", "word": "",
                         "message": "パスワードやAPIキーのような文字列があります。AIに送る前に消してください。"})
    return warnings


def contains_secret(text):
    return any(re.search(p, text) for p in review_rules()["secret_patterns"])
