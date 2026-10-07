// Digest front end: loads news.json, ranks it with the on-device model and
// renders one card per story. Works the same in a browser and inside the
// Android app (Capacitor).
(function () {
  "use strict";

  const FOR_YOU = "For you";
  const ACCENTS = {
    [FOR_YOU]: "var(--forYou)",
    "Sri Lanka": "var(--sriLanka)",
    "Mannar": "var(--mannar)",
    "World": "var(--world)",
    "Technology": "var(--technology)",
    "Business": "var(--business)",
    "Sports": "var(--sports)",
    "Science": "var(--science)",
  };
  const FOR_YOU_LIMIT = 60;
  const PLATFORM_LABELS = { reddit: "Reddit", mastodon: "Mastodon", youtube: "YouTube", bluesky: "Bluesky" };

  const cap = window.Capacitor;
  const isNative = !!(cap && cap.isNativePlatform && cap.isNativePlatform());
  const config = window.DIGEST_CONFIG || {};
  const newsUrl = isNative || location.protocol === "file:" ? config.remoteNewsUrl : "news.json";

  const tabsEl = document.getElementById("tabs");
  const mainEl = document.getElementById("main");
  const updatedEl = document.getElementById("updated");
  const refreshBtn = document.getElementById("refreshBtn");
  const settingsBtn = document.getElementById("settingsBtn");
  const sortHint = document.getElementById("sortHint");
  const sortButtons = Array.from(document.querySelectorAll(".segmented button"));

  // ---------------------------------------------------------------------
  // Storage. Everything personal (reactions, hidden stories, blocked
  // sources) lives on this device only.
  // ---------------------------------------------------------------------
  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem("digest." + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (err) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem("digest." + key, JSON.stringify(value));
      } catch (err) {
        console.warn("Storage unavailable", err);
      }
    },
  };

  const state = {
    digest: null,
    offline: false,
    defaults: null,
    model: null,
    events: store.get("events", []),
    hidden: new Set(store.get("hidden", [])),
    reactions: store.get("reactions", {}),
    blocked: new Set(store.get("blocked", [])),
    prefs: Object.assign({ tab: FOR_YOU, sort: "relevant", showSocialOnly: true }, store.get("prefs", {})),
    implicit: new Set(),
  };

  function savePrefs() { store.set("prefs", state.prefs); }

  // ---------------------------------------------------------------------
  // Model.
  // ---------------------------------------------------------------------
  async function loadModel() {
    try {
      const res = await fetch("model_defaults.json");
      state.defaults = DigestModel.fromConfig(await res.json());
    } catch (err) {
      console.warn("Model defaults unavailable; ranking by time", err);
      state.defaults = null;
    }
    retrain();
  }

  function retrain() {
    if (!state.defaults) return;
    state.model = DigestModel.train(state.defaults, state.events);
  }

  function record(story, reaction) {
    if (!state.defaults) return;
    const now = Date.now() / 1000;
    const explicit = ["interested", "moderate", "not_interested"];
    if (explicit.includes(reaction)) {
      // A new explicit reaction replaces the previous one for this story.
      state.events = state.events.filter((e) => !(e.id === story.id && explicit.includes(e.reaction)));
      state.reactions[story.id] = reaction;
      store.set("reactions", state.reactions);
    } else {
      const key = reaction + ":" + story.id;
      if (state.implicit.has(key)) return;
      state.implicit.add(key);
    }
    state.events.push(DigestModel.makeEvent(story, reaction, now));
    const max = state.defaults.config.max_events || 500;
    if (state.events.length > max) state.events = state.events.slice(-max);
    store.set("events", state.events);
    retrain();
  }

  function unrecord(story) {
    state.events = state.events.filter((e) => e.id !== story.id || !["not_interested"].includes(e.reaction));
    delete state.reactions[story.id];
    store.set("events", state.events);
    store.set("reactions", state.reactions);
    retrain();
  }

  // ---------------------------------------------------------------------
  // Data.
  // ---------------------------------------------------------------------
  async function fetchDigest(force) {
    setRefreshing(true);
    try {
      const url = newsUrl + (force ? (newsUrl.includes("?") ? "&" : "?") + "t=" + Date.now() : "");
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const digest = await res.json();
      if (!digest || !digest.stories) throw new Error("Unexpected news.json format");
      state.digest = digest;
      state.offline = false;
      store.set("news", digest);
    } catch (err) {
      console.warn("News fetch failed", err);
      state.offline = true;
      if (!state.digest) state.digest = store.get("news", null);
    } finally {
      setRefreshing(false);
    }
    render();
  }

  function topics() {
    return [FOR_YOU].concat((state.digest && state.digest.topics) || []);
  }

  function isBlocked(story) {
    if (state.blocked.has(story.best_source)) return true;
    const names = (story.sources || []).map((s) => s.name);
    return names.length > 0 && names.every((n) => state.blocked.has(n));
  }

  function visibleStories(tab) {
    const all = state.digest ? state.digest.stories : {};
    let stories;
    if (tab === FOR_YOU) {
      const seen = new Set();
      stories = [];
      for (const topic of state.digest.topics || []) {
        for (const story of all[topic] || []) {
          const key = story.best_link || story.id;
          if (seen.has(story.id) || seen.has(key)) continue;
          seen.add(story.id);
          seen.add(key);
          stories.push(story);
        }
      }
    } else {
      stories = (all[tab] || []).slice();
    }
    stories = stories.filter((s) =>
      !state.hidden.has(s.id) && !isBlocked(s) && (state.prefs.showSocialOnly || !s.features.is_social_only));

    const now = Date.now() / 1000;
    if (state.prefs.sort === "latest" || !state.model) {
      stories.sort((a, b) => b.published_ts - a.published_ts);
    } else {
      const scores = new Map(stories.map((s) => [s.id, DigestModel.scoreStory(state.model, s, now)]));
      stories.sort((a, b) => scores.get(b.id) - scores.get(a.id));
      if (tab === FOR_YOU) stories = diversify(stories, scores);
    }
    return tab === FOR_YOU ? stories.slice(0, FOR_YOU_LIMIT) : stories;
  }

  // Mix categories in "For you": each further story from a topic that is
  // already in the list counts a little less, so one topic can't take over.
  function diversify(sorted, scores) {
    const picked = [];
    const perTopic = new Map();
    const remaining = sorted.slice();
    while (remaining.length && picked.length < FOR_YOU_LIMIT) {
      let bestIndex = 0;
      let bestValue = -Infinity;
      for (let i = 0; i < remaining.length; i++) {
        const s = remaining[i];
        const value = scores.get(s.id) * Math.pow(0.85, perTopic.get(s.topic) || 0);
        if (value > bestValue) { bestValue = value; bestIndex = i; }
      }
      const [story] = remaining.splice(bestIndex, 1);
      perTopic.set(story.topic, (perTopic.get(story.topic) || 0) + 1);
      picked.push(story);
    }
    return picked;
  }

  // ---------------------------------------------------------------------
  // Rendering helpers.
  // ---------------------------------------------------------------------
  function el(tag, props, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else if (key === "style") node.setAttribute("style", value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of [].concat(children || [])) {
      if (child) node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function timeAgo(ts) {
    if (!ts) return "";
    const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    return Math.round(hrs / 24) + "d ago";
  }

  async function openExternal(url) {
    if (isNative && cap.nativePromise) {
      try {
        // @capacitor/browser via the native bridge (the app has no JS bundler,
        // so plugin proxies from @capacitor/core aren't loaded).
        await cap.nativePromise("Browser", "open", { url });
        return;
      } catch (err) {
        console.warn("In-app browser unavailable", err);
      }
    }
    window.open(url, "_blank", "noopener");
  }

  function externalLink(url, props, children, onOpen) {
    return el("a", Object.assign({
      href: url,
      target: "_blank",
      rel: "noopener noreferrer",
      onclick: (event) => {
        if (onOpen) onOpen();
        if (isNative) {
          event.preventDefault();
          openExternal(url);
        }
      },
    }, props), children);
  }

  let toastTimer = null;
  function toast(message, actionLabel, action) {
    document.querySelectorAll(".toast").forEach((t) => t.remove());
    clearTimeout(toastTimer);
    const node = el("div", { className: "toast", role: "status" }, [
      el("span", { text: message }),
      actionLabel ? el("button", { type: "button", text: actionLabel, onclick: () => { node.remove(); action(); } }) : null,
    ]);
    document.body.appendChild(node);
    toastTimer = setTimeout(() => node.remove(), 4000);
  }

  // ---------------------------------------------------------------------
  // Cards.
  // ---------------------------------------------------------------------
  function storyCard(story, tab) {
    const accent = ACCENTS[story.topic] || "var(--ink)";
    const sourceCount = story.features.source_count;
    const socialCount = story.features.social_count;
    const openStory = () => record(story, "opened");

    const meta = el("div", { className: "meta" }, [
      tab === FOR_YOU ? el("span", { className: "topic-chip", text: story.topic }) : null,
      story.focus_label ? el("span", { className: "badge focus", text: story.focus_label }) : null,
      el("span", { className: "source", text: story.best_source || "" }),
      el("span", { text: timeAgo(story.published_ts) }),
      sourceCount > 1 ? el("span", { className: "badge", text: sourceCount + " sources" }) : null,
      socialCount > 0 ? el("span", { className: "badge", text: "💬 " + socialCount }) : null,
    ]);

    const title = el("h2", {}, story.best_link
      ? externalLink(story.best_link, {}, story.title, openStory)
      : story.title);
    const description = story.description ? el("p", { className: "description", text: story.description }) : null;

    const details = el("div", { className: "details", hidden: true });
    const moreBtn = el("button", { type: "button", className: "more-btn", text: "Read more" });
    moreBtn.addEventListener("click", () => {
      const opening = details.hidden;
      if (opening && !details.childNodes.length) fillDetails(details, story);
      details.hidden = !opening;
      if (description) description.hidden = opening;
      moreBtn.textContent = opening ? "Show less" : "Read more";
      if (opening) record(story, "expanded");
    });

    const card = el("article", { className: "card", style: "--accent:" + accent });
    const feedback = [
      { label: "interested", text: "👍 Interested", title: "Interested" },
      { label: "moderate", text: "Moderate", title: "Moderately interested" },
      { label: "not_interested", text: "👎 Not for me", title: "Not interested" },
    ].map(({ label, text, title }) => {
      const selected = state.reactions[story.id] === label;
      return el("button", {
        type: "button",
        className: "fb-btn " + label + (selected ? " selected" : ""),
        text,
        title,
        "aria-label": title,
        "aria-pressed": selected ? "true" : "false",
        onclick: () => react(story, label, card),
      });
    });

    card.append(meta, title);
    if (description) card.append(description);
    // Some feeds (e.g. Google News) give only a headline; then there is
    // nothing to expand, so offer the article itself.
    const hasText = story.description || story.full_summary;
    const primary = hasText || !story.best_link
      ? moreBtn
      : externalLink(story.best_link, { className: "more-btn" }, "Read at " + (story.best_source || "source") + " →", openStory);
    card.append(details, el("div", { className: "actions" }, [primary, el("div", { className: "feedback" }, feedback)]));
    return card;
  }

  function fillDetails(details, story) {
    const sourceCount = story.features.source_count;
    const heading = sourceCount > 1 ? "Summary from " + sourceCount + " sources" : "Summary";
    details.append(el("h3", { text: heading }));
    details.append(el("p", { text: story.full_summary || story.description || "No summary is available for this story yet." }));
    if (story.summary_by === "ai") {
      details.append(el("p", { className: "ai-note", text: "Summary written by AI from the reports below. Check the original for details." }));
    }
    if (story.best_link) {
      details.append(externalLink(story.best_link, { className: "read-full" },
        "Read full article" + (story.best_source ? " at " + story.best_source : "") + " →",
        () => record(story, "opened")));
    }
    const others = (story.sources || []).filter((s) => s.url && s.url !== story.best_link);
    if (others.length) {
      details.append(el("h3", { text: "Also reported by" }));
      details.append(el("ul", { className: "link-list" }, others.map((s) =>
        el("li", {}, externalLink(s.url, {}, s.name + " · " + timeAgo(Date.parse(s.published) / 1000))))));
    }
    if ((story.social || []).length) {
      details.append(el("h3", { text: "Discussed on social media" }));
      details.append(el("ul", { className: "link-list" }, story.social.slice(0, 8).map((s) =>
        el("li", {}, [
          el("span", { className: "platform", text: PLATFORM_LABELS[s.platform] || s.platform }),
          externalLink(s.url, {}, s.name),
        ]))));
    }
  }

  function react(story, label, card) {
    if (label === "not_interested") {
      record(story, label);
      state.hidden.add(story.id);
      store.set("hidden", Array.from(state.hidden));
      card.remove();
      toast("Hidden. You'll see less like this.", "Undo", () => {
        state.hidden.delete(story.id);
        store.set("hidden", Array.from(state.hidden));
        unrecord(story);
        render();
      });
      return;
    }
    record(story, label);
    card.querySelectorAll(".fb-btn").forEach((btn) => {
      const on = btn.classList.contains(label);
      btn.classList.toggle("selected", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    toast(label === "interested" ? "Got it. More stories like this." : "Noted.");
  }

  // ---------------------------------------------------------------------
  // Page.
  // ---------------------------------------------------------------------
  function renderTabs() {
    tabsEl.innerHTML = "";
    for (const topic of topics()) {
      const tab = el("button", {
        type: "button",
        className: "tab" + (topic === state.prefs.tab ? " active" : ""),
        text: topic,
        style: "--accent:" + (ACCENTS[topic] || "var(--ink)"),
        onclick: () => {
          state.prefs.tab = topic;
          savePrefs();
          render();
          window.scrollTo({ top: 0 });
        },
      });
      tabsEl.appendChild(tab);
    }
    const active = tabsEl.querySelector(".tab.active");
    if (active && active.scrollIntoView) active.scrollIntoView({ inline: "nearest", block: "nearest" });
  }

  function renderUpdated() {
    if (!state.digest) {
      updatedEl.textContent = state.offline ? "Offline" : "Loading…";
      updatedEl.classList.toggle("offline", state.offline);
      return;
    }
    const age = timeAgo(state.digest.generated_at);
    updatedEl.textContent = state.offline ? "Offline · news from " + age : "Updated " + age;
    updatedEl.classList.toggle("offline", state.offline);
  }

  function renderState(message, retry) {
    mainEl.innerHTML = "";
    mainEl.appendChild(el("div", { className: "state" }, [
      message,
      retry ? el("br") : null,
      retry ? el("button", { type: "button", text: "Try again", onclick: () => fetchDigest(true) }) : null,
    ]));
  }

  function render() {
    renderUpdated();
    sortButtons.forEach((b) => b.classList.toggle("active", b.dataset.sort === state.prefs.sort));
    sortHint.textContent = state.prefs.sort === "latest" ? "Newest first" : "Most relevant to you first";
    if (!state.digest) {
      renderTabs();
      renderState(state.offline ? "Couldn't load the news. Check your connection." : "Loading today's news…", state.offline);
      return;
    }
    if (!topics().includes(state.prefs.tab)) state.prefs.tab = FOR_YOU;
    renderTabs();
    const stories = visibleStories(state.prefs.tab);
    mainEl.innerHTML = "";
    if (!stories.length) {
      renderState("No stories here right now.", true);
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const story of stories) fragment.appendChild(storyCard(story, state.prefs.tab));
    mainEl.appendChild(fragment);
  }

  function setRefreshing(on) { refreshBtn.classList.toggle("spinning", on); }

  // ---------------------------------------------------------------------
  // Settings sheet.
  // ---------------------------------------------------------------------
  function sourceNames() {
    const counts = new Map();
    for (const stories of Object.values((state.digest && state.digest.stories) || {})) {
      for (const story of stories) {
        for (const s of story.sources || []) counts.set(s.name, (counts.get(s.name) || 0) + 1);
      }
    }
    for (const name of state.blocked) if (!counts.has(name)) counts.set(name, 0);
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 60).map((e) => e[0]);
  }

  function openSettings() {
    const close = () => { backdrop.remove(); sheet.remove(); render(); };
    const backdrop = el("div", { className: "sheet-backdrop", onclick: close });
    const reactions = state.events.filter((e) => ["interested", "moderate", "not_interested"].includes(e.reaction)).length;

    const socialToggle = el("input", { type: "checkbox", checked: state.prefs.showSocialOnly ? true : null });
    socialToggle.checked = state.prefs.showSocialOnly;
    socialToggle.addEventListener("change", () => { state.prefs.showSocialOnly = socialToggle.checked; savePrefs(); });

    const sourceGrid = el("div", { className: "source-grid" }, sourceNames().map((name) => {
      const box = el("input", { type: "checkbox" });
      box.checked = !state.blocked.has(name);
      box.addEventListener("change", () => {
        if (box.checked) state.blocked.delete(name); else state.blocked.add(name);
        store.set("blocked", Array.from(state.blocked));
      });
      return el("label", {}, [box, name]);
    }));

    const sheet = el("section", { className: "settings", role: "dialog", "aria-label": "Settings" }, [
      el("button", { type: "button", className: "secondary close", text: "Done", onclick: close }),
      el("h2", { text: "Settings" }),
      el("h3", { text: "Your ranking" }),
      el("p", { text: "Digest learns what you like from 👍 / 👎 and from the stories you open or expand. It has learned from " + reactions + " reaction" + (reactions === 1 ? "" : "s") + " so far. Your reactions stay on this device." }),
      el("button", {
        type: "button",
        className: "secondary",
        text: "Reset learning",
        onclick: () => {
          if (!confirm("Forget all reactions and hidden stories?")) return;
          state.events = [];
          state.reactions = {};
          state.hidden = new Set();
          store.set("events", []);
          store.set("reactions", {});
          store.set("hidden", []);
          retrain();
          close();
          toast("Ranking reset.");
        },
      }),
      el("h3", { text: "Feed" }),
      el("label", { className: "setting-row" }, [el("span", { text: "Show stories found only on social media" }), socialToggle]),
      el("h3", { text: "Sources" }),
      el("p", { text: "Untick a source to hide its stories." }),
      sourceGrid,
      el("h3", { text: "About" }),
      el("p", { text: state.digest ? "News updated " + timeAgo(state.digest.generated_at) + ". New stories are collected about every 30 minutes." : "" }),
      config.releasesUrl ? externalLink(config.releasesUrl, {}, "Get the latest Android app") : null,
    ]);
    document.body.append(backdrop, sheet);
  }

  // ---------------------------------------------------------------------
  // Start.
  // ---------------------------------------------------------------------
  refreshBtn.addEventListener("click", () => fetchDigest(true));
  settingsBtn.addEventListener("click", openSettings);
  sortButtons.forEach((button) => button.addEventListener("click", () => {
    state.prefs.sort = button.dataset.sort;
    savePrefs();
    render();
  }));
  document.addEventListener("visibilitychange", () => {
    // Coming back to the app after a while: pick up the latest digest.
    if (document.visibilityState === "visible" && state.digest && Date.now() / 1000 - state.digest.generated_at > 30 * 60) {
      fetchDigest(false);
    }
  });

  async function init() {
    state.digest = store.get("news", null);
    await loadModel();
    if (state.digest) render();
    await fetchDigest(false);
  }

  if (!isNative && "serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }

  init();
})();
