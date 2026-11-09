// 章ごとの再生。再生位置は端末のブラウザ（localStorage）に保存し、次に開いたとき続きから再生する。
(() => {
  const book = window.BOOK;
  const audio = document.getElementById("audio");
  const $ = (id) => document.getElementById(id);
  const player = $("player"), seek = $("seek"), playBtn = $("play");
  const posKey = "pos:" + book.id, rateKey = "rate";
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const fmt = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  };
  let current = null;

  function chapterTimes() { return store.get("times:" + book.id) || {}; }
  function showProgress() {
    const t = chapterTimes();
    document.querySelectorAll(".progress").forEach((el) => {
      const s = t[el.dataset.file];
      el.textContent = s ? (s.done ? "聞き終わり" : `${fmt(s.t)} まで聞きました`) : "";
    });
  }
  function save(done) {
    if (!current) return;
    const t = chapterTimes();
    t[current.file] = { t: audio.currentTime, done: !!done };
    store.set("times:" + book.id, t);
    store.set(posKey, { file: current.file, title: current.title, t: audio.currentTime, at: Date.now() });
  }
  function load(btn, autoplay, at) {
    document.querySelectorAll(".chapter.playing").forEach((b) => b.classList.remove("playing"));
    btn.classList.add("playing");
    current = { file: btn.dataset.file, title: btn.dataset.title, btn };
    player.hidden = false;
    $("now").textContent = current.title;
    audio.src = `/media/${encodeURIComponent(book.id)}/${encodeURIComponent(current.file)}`;
    audio.playbackRate = store.get(rateKey) || 1;
    const saved = chapterTimes()[current.file];
    const start = at ?? (saved && !saved.done ? saved.t : 0);
    audio.addEventListener("loadedmetadata", () => { audio.currentTime = start; }, { once: true });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({ title: current.title, artist: book.author, album: book.title });
    }
    if (autoplay) audio.play().catch(() => {});
  }
  function next() {
    const all = [...document.querySelectorAll(".chapter[data-file]")];
    const i = all.indexOf(current && current.btn);
    if (i >= 0 && i + 1 < all.length) load(all[i + 1], true, 0);
  }
  function setRate(r) {
    audio.playbackRate = r;
    store.set(rateKey, r);
    document.querySelectorAll(".speeds button").forEach((b) => b.classList.toggle("on", Number(b.dataset.rate) === r));
  }

  document.querySelectorAll(".chapter[data-file]").forEach((btn) =>
    btn.addEventListener("click", () => { save(); load(btn, true); }));
  document.querySelectorAll(".speeds button").forEach((b) =>
    b.addEventListener("click", () => setRate(Number(b.dataset.rate))));
  playBtn.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  $("back").addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
  $("fwd").addEventListener("click", () => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 30); });
  seek.addEventListener("input", () => { audio.currentTime = Number(seek.value); });

  audio.addEventListener("play", () => { playBtn.textContent = "一時停止"; });
  audio.addEventListener("pause", () => { playBtn.textContent = "再生"; save(); showProgress(); });
  audio.addEventListener("loadedmetadata", () => { seek.max = Math.floor(audio.duration); $("dur").textContent = fmt(audio.duration); });
  let lastSave = 0;
  audio.addEventListener("timeupdate", () => {
    seek.value = Math.floor(audio.currentTime);
    $("cur").textContent = fmt(audio.currentTime);
    if (Date.now() - lastSave > 5000) { lastSave = Date.now(); save(); }
  });
  audio.addEventListener("ended", () => { save(true); showProgress(); next(); });
  window.addEventListener("pagehide", () => save());

  if ("mediaSession" in navigator) {
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => audio.play());
    ms.setActionHandler("pause", () => audio.pause());
    ms.setActionHandler("seekbackward", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
    ms.setActionHandler("seekforward", () => { audio.currentTime += 30; });
    ms.setActionHandler("nexttrack", next);
  }

  setRate(store.get(rateKey) || 1);
  showProgress();
  // 前回の続きを読み込んでおく（自動再生は iPhone で止められるので、再生ボタンで始める）
  const last = store.get(posKey);
  const btn = last && [...document.querySelectorAll(".chapter[data-file]")].find((b) => b.dataset.file === last.file);
  if (btn) load(btn, false, last.t);
})();
