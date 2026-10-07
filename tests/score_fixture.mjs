// Prints the scores static/model.js gives the fixture stories after training
// on the fixture events. test_news_ranking.py compares them with ranking.py.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const model = require("../static/model.js");
const root = new URL("..", import.meta.url);
const defaults = model.fromConfig(JSON.parse(readFileSync(new URL("static/model_defaults.json", root), "utf8")));
const fixture = JSON.parse(readFileSync(new URL("tests/fixtures/model_fixture.json", root), "utf8"));

const events = fixture.events.map((e) => model.makeEvent(fixture.stories[e.story], e.reaction, fixture.now));
const trained = model.train(defaults, events);
console.log(JSON.stringify(fixture.stories.map((s) => model.scoreStory(trained, s, fixture.now))));
