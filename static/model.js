// On-device relevance model: a logistic regression trained on the reader's
// own reactions. Mirrors ranking.py exactly (same features, hashing and
// training loop) so both can be tested against each other.
(function (root) {
  "use strict";

  const LABELS = {
    // reaction -> [target, sample weight]
    interested: [1.0, 1.0],
    moderate: [0.5, 1.0],
    not_interested: [0.0, 1.0],
    opened: [1.0, 0.5],
    expanded: [0.7, 0.3],
  };

  const encoder = new TextEncoder();

  function fnv1a(text) {
    let h = 0x811c9dc5;
    for (const byte of encoder.encode(text)) {
      h ^= byte;
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
  }

  function fromConfig(config) {
    const buckets = config.hash_buckets;
    const sparse = new Array(buckets).fill(0);
    for (const [key, weight] of Object.entries(config.sparse_weights)) {
      sparse[fnv1a(key) % buckets] += weight;
    }
    return { config, dense: config.dense_weights.slice(), sparse };
  }

  function featurize(story, now, tauHours) {
    const tau = tauHours || 18;
    const f = story.features || {};
    const published = Number(story.published_ts || now);
    const ageHours = Math.max(0, (now - published) / 3600);
    const dense = [
      1.0,
      Math.exp(-ageHours / tau),
      Math.log1p(f.source_count || 0),
      Math.log1p(f.social_count || 0),
      Number(f.source_trust ?? 0.6),
      Number(f.local_source || 0),
      Number(f.local_mention || 0),
      Number(f.is_social_only || 0),
    ];
    const sparse = [["t:" + String(story.topic || ""), 1.0]];
    if (story.focus) sparse.push(["f:" + String(story.topic || ""), 1.0]);
    if (story.best_source) sparse.push(["s:" + String(story.best_source).toLowerCase(), 1.0]);
    const keywords = story.keywords || [];
    if (keywords.length) {
      const value = 1.0 / Math.sqrt(keywords.length);
      for (const keyword of keywords) sparse.push(["k:" + keyword, value]);
    }
    return { dense, sparse };
  }

  function sigmoid(z) {
    if (z >= 0) return 1 / (1 + Math.exp(-z));
    const e = Math.exp(z);
    return e / (1 + e);
  }

  function score(model, x) {
    const buckets = model.sparse.length;
    let z = 0;
    for (let i = 0; i < model.dense.length; i++) z += model.dense[i] * x.dense[i];
    for (const [key, value] of x.sparse) z += model.sparse[fnv1a(key) % buckets] * value;
    return sigmoid(z);
  }

  function scoreStory(model, story, now) {
    return score(model, featurize(story, now, model.config.recency_tau_hours));
  }

  // Retrain from the defaults over the reaction log, oldest first. With a few
  // hundred events this takes a couple of milliseconds.
  function train(defaults, events) {
    const { learning_rate: lr, l2, epochs } = defaults.config;
    const denseLr = lr * (defaults.config.dense_learning_rate_scale ?? 1.0);
    const dense0 = defaults.dense;
    const sparse0 = defaults.sparse;
    const model = { config: defaults.config, dense: dense0.slice(), sparse: sparse0.slice() };
    const buckets = model.sparse.length;
    for (let epoch = 0; epoch < epochs; epoch++) {
      for (const event of events) {
        const x = event.x;
        const g = event.w * (score(model, x) - event.y);
        for (let i = 0; i < x.dense.length; i++) {
          model.dense[i] -= denseLr * (g * x.dense[i] + l2 * (model.dense[i] - dense0[i]));
        }
        for (const [key, value] of x.sparse) {
          const b = fnv1a(key) % buckets;
          model.sparse[b] -= lr * (g * value + l2 * (model.sparse[b] - sparse0[b]));
        }
      }
    }
    return model;
  }

  function makeEvent(story, reaction, now) {
    const [y, w] = LABELS[reaction];
    return { x: featurize(story, now), y, w, reaction, id: story.id, t: now };
  }

  const api = { LABELS, fnv1a, fromConfig, featurize, score, scoreStory, train, makeEvent };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DigestModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
