// Unit tests for the on-device model: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const M = require("../static/model.js");
const config = JSON.parse(readFileSync(new URL("../static/model_defaults.json", import.meta.url), "utf8"));
const NOW = 1_790_000_000;

function story(id, keywords, hoursOld = 2, topic = "World") {
  return {
    id, topic, keywords, best_source: "BBC", published_ts: NOW - hoursOld * 3600,
    features: { source_trust: 0.9, source_count: 1, social_count: 0, local_source: 0, local_mention: 0, is_social_only: 0 },
  };
}

test("fnv1a matches reference values", () => {
  assert.equal(M.fnv1a(""), 0x811c9dc5);
  assert.equal(M.fnv1a("a"), 0xe40c292c);
  assert.equal(M.fnv1a("foobar"), 0xbf9cf968);
});

test("interested raises scores of similar stories", () => {
  const defaults = M.fromConfig(config);
  const liked = story("a", ["budget", "parliament"]);
  const similar = story("b", ["budget", "debate"]);
  const trained = M.train(defaults, [M.makeEvent(liked, "interested", NOW)]);
  assert.ok(M.scoreStory(trained, similar, NOW) > M.scoreStory(defaults, similar, NOW));
});

test("not interested lowers scores of similar stories", () => {
  const defaults = M.fromConfig(config);
  const disliked = story("a", ["liverpool", "city"], 1, "Sports");
  const similar = story("b", ["liverpool", "manager"], 3, "Sports");
  const trained = M.train(defaults, [M.makeEvent(disliked, "not_interested", NOW)]);
  assert.ok(M.scoreStory(trained, similar, NOW) < M.scoreStory(defaults, similar, NOW));
});

test("training does not mutate the defaults", () => {
  const defaults = M.fromConfig(config);
  const dense = defaults.dense.slice();
  M.train(defaults, [M.makeEvent(story("a", ["x"]), "interested", NOW)]);
  assert.deepEqual(defaults.dense, dense);
});

test("fresher stories score higher with all else equal", () => {
  const defaults = M.fromConfig(config);
  assert.ok(M.scoreStory(defaults, story("new", ["x"], 1), NOW) > M.scoreStory(defaults, story("old", ["x"], 40), NOW));
});
