// Where the app loads its news from.
// - The Android app (and anything not served by our own server) reads the
//   digest that GitHub Actions publishes to GitHub Pages every 30 minutes.
// - The local dev server and the GitHub Pages site serve news.json next to
//   the page, so they use a relative URL.
window.DIGEST_CONFIG = {
  remoteNewsUrl: "https://jatheel.github.io/Digest/news.json",
  releasesUrl: "https://github.com/Jatheel/Digest/releases/latest",
};
