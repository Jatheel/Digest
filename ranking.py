"""
Relevance model
---------------
A small logistic regression that predicts how likely the reader is to care
about a story. The phone app runs the same model in static/model.js and trains
it on the reader's own reactions; this Python copy orders stories in
news.json (default weights) and keeps the two implementations testable.

Features per story:
  dense  - bias, recency, log(1 + #outlets), log(1 + #social posts), source
           trust, local outlet, mentions Sri Lanka, social-only
  sparse - topic, topic focus (football in Sports, AI & IT in Technology),
           best source and headline keywords, hashed (FNV-1a) into a
           fixed number of buckets so the model can learn interests without a
           vocabulary.

Training is plain SGD on log-loss with an L2 pull toward the default weights,
so a handful of taps nudges the ranking instead of overturning it. The shared
dense weights learn 10x slower than the sparse ones, so one "not interested"
on a football story mostly penalizes football, not every story.
"""

import json
import math
from pathlib import Path

DEFAULTS_PATH = Path(__file__).with_name("static") / "model_defaults.json"

LABELS = {
    # reaction -> (target, sample weight)
    "interested": (1.0, 1.0),
    "moderate": (0.5, 1.0),
    "not_interested": (0.0, 1.0),
    "opened": (1.0, 0.5),
    "expanded": (0.7, 0.3),
}


def fnv1a(text: str) -> int:
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def load_defaults(path: Path = DEFAULTS_PATH) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    buckets = config["hash_buckets"]
    sparse = [0.0] * buckets
    for key, weight in config["sparse_weights"].items():
        sparse[fnv1a(key) % buckets] += weight
    return {"config": config, "dense": list(config["dense_weights"]), "sparse": sparse}


def featurize(story: dict, now: float, tau_hours: float = 18.0) -> dict:
    features = story.get("features", {})
    age_hours = max(0.0, (now - float(story.get("published_ts") or now)) / 3600.0)
    dense = [
        1.0,
        math.exp(-age_hours / tau_hours),
        math.log1p(features.get("source_count", 0)),
        math.log1p(features.get("social_count", 0)),
        float(features.get("source_trust", 0.6)),
        float(features.get("local_source", 0)),
        float(features.get("local_mention", 0)),
        float(features.get("is_social_only", 0)),
    ]
    sparse = [["t:" + str(story.get("topic", "")), 1.0]]
    if story.get("focus"):
        sparse.append(["f:" + str(story.get("topic", "")), 1.0])
    if story.get("best_source"):
        sparse.append(["s:" + str(story["best_source"]).lower(), 1.0])
    keywords = story.get("keywords") or []
    if keywords:
        value = 1.0 / math.sqrt(len(keywords))
        sparse.extend(["k:" + keyword, value] for keyword in keywords)
    return {"dense": dense, "sparse": sparse}


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def score(model: dict, x: dict) -> float:
    buckets = len(model["sparse"])
    z = 0.0
    for weight, value in zip(model["dense"], x["dense"]):
        z += weight * value
    for key, value in x["sparse"]:
        z += model["sparse"][fnv1a(key) % buckets] * value
    return _sigmoid(z)


def score_story(model: dict, story: dict, now: float) -> float:
    tau = model["config"].get("recency_tau_hours", 18)
    return score(model, featurize(story, now, tau))


def train(defaults: dict, events: list[dict]) -> dict:
    """Retrain from the defaults over the reaction log (oldest first).

    Each event is {"x": featurize(...), "y": target, "w": sample weight}.
    """
    config = defaults["config"]
    lr, l2 = config["learning_rate"], config["l2"]
    # Shared features (bias, recency, trust...) learn slowly; personal taste
    # is carried by the sparse topic/source/keyword weights.
    dense_lr = lr * config.get("dense_learning_rate_scale", 1.0)
    dense0, sparse0 = defaults["dense"], defaults["sparse"]
    model = {"config": config, "dense": list(dense0), "sparse": list(sparse0)}
    buckets = len(model["sparse"])
    for _ in range(config["epochs"]):
        for event in events:
            x = event["x"]
            g = event["w"] * (score(model, x) - event["y"])
            for i, value in enumerate(x["dense"]):
                model["dense"][i] -= dense_lr * (g * value + l2 * (model["dense"][i] - dense0[i]))
            for key, value in x["sparse"]:
                b = fnv1a(key) % buckets
                model["sparse"][b] -= lr * (g * value + l2 * (model["sparse"][b] - sparse0[b]))
    return model


def make_event(story: dict, reaction: str, now: float) -> dict:
    target, weight = LABELS[reaction]
    return {"x": featurize(story, now), "y": target, "w": weight, "reaction": reaction}
