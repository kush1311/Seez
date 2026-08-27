from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from seezar_operator import llm, report
from seezar_operator.config import TOPICS

GOLD_PATH = BASE_DIR / "eval" / "gold_labels.json"


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def score(gold: list, predicted: list) -> dict:
    truth = [g["label"] for g in gold]
    correct = sum(1 for t, p in zip(truth, predicted) if t == p)

    tp, fp, fn = Counter(), Counter(), Counter()
    for t, p in zip(truth, predicted):
        if t == p:
            tp[t] += 1
        else:
            fp[p] += 1
            fn[t] += 1

    per_class = {}
    for label in sorted(set(truth) | set(predicted)):
        precision = tp[label] / (tp[label] + fp[label]) if (tp[label] + fp[label]) else 0.0
        recall = tp[label] / (tp[label] + fn[label]) if (tp[label] + fn[label]) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[label] = {"support": truth.count(label), "precision": precision,
                            "recall": recall, "f1": f1}

    confusion = defaultdict(Counter)
    for t, p in zip(truth, predicted):
        confusion[t][p] += 1

    macro = (sum(v["f1"] for v in per_class.values()) / len(per_class)) if per_class else 0.0
    return {"n": len(gold), "correct": correct, "accuracy": correct / len(gold),
            "macro_f1": macro, "per_class": per_class, "confusion": confusion,
            "truth": truth, "predicted": predicted}


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure topic-classifier accuracy against hand labels")
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--save", action="store_true", help="Write a Markdown report")
    args = ap.parse_args()

    _force_utf8_stdout()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    messages = [g["message"] for g in gold]

    print("Classifying %d hand-labelled messages with %s ...\n" % (len(messages), llm.OPENROUTER_MODEL))
    predicted = [r["topic"] for r in llm.analyse(messages)]
    res = score(gold, predicted)

    report.header("Topic classifier evaluation")
    report.table("Overall", ["Metric", "Value"], [
        ["Messages", res["n"]],
        ["Correct", res["correct"]],
        ["Accuracy", "%.1f%%" % (100 * res["accuracy"])],
        ["Macro F1", "%.3f" % res["macro_f1"]],
    ])
    report.table("Per class", ["Topic", "Support", "Precision", "Recall", "F1"],
                 [[k, v["support"], "%.2f" % v["precision"], "%.2f" % v["recall"], "%.2f" % v["f1"]]
                  for k, v in sorted(res["per_class"].items(), key=lambda kv: -kv[1]["support"])])

    misses = [(g["message"], t, p) for g, t, p in zip(gold, res["truth"], res["predicted"]) if t != p]
    if misses:
        report.table("Disagreements", ["Message", "Hand label", "Model"],
                     [[m[:58], t, p] for m, t, p in misses])

    if args.save:
        md = ["# Topic Classifier Evaluation", "",
              "**Model:** `%s`  " % llm.OPENROUTER_MODEL,
              "**Gold set:** %d messages, labelled by hand from the live Ejner Hessel export  " % res["n"],
              "**Accuracy:** %.1f%% (%d/%d)  " % (100 * res["accuracy"], res["correct"], res["n"]),
              "**Macro F1:** %.3f" % res["macro_f1"], "",
              "## Per class", "",
              report.md_table(["Topic", "Support", "Precision", "Recall", "F1"],
                              [[k, v["support"], "%.2f" % v["precision"], "%.2f" % v["recall"],
                                "%.2f" % v["f1"]]
                               for k, v in sorted(res["per_class"].items(),
                                                  key=lambda kv: -kv[1]["support"])]), ""]
        if misses:
            md += ["## Disagreements", "",
                   report.md_table(["Message", "Hand label", "Model"],
                                   [[m.replace("|", "/")[:70], t, p] for m, t, p in misses]), ""]
        md += ["---", "",
               "Labels were assigned by reading each message; they are one annotator's",
               "judgement, not an adjudicated gold standard. Taxonomy: %s." % ", ".join(TOPICS), ""]
        report.save("topic_classifier_evaluation", "\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
