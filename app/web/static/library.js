// ライブラリ: 端末に保存した再生位置から、進み具合・残り時間・「続きを聞く」を出す
(() => {
  const get = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } };
  const left = (sec) => {
    sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec / 3600), m = Math.round(sec % 3600 / 60);
    return h ? `残り ${h}時間${m}分` : `残り ${m}分`;
  };
  let latest = null;
  document.querySelectorAll(".title-row").forEach((row) => {
    const id = row.dataset.book, total = Number(row.dataset.total) || 0;
    const files = JSON.parse(row.dataset.files || "[]");
    const times = get("times:" + id) || {};
    let heard = 0, any = false, allDone = files.length > 0;
    for (const f of files) {
      const t = times[f.file];
      if (t) any = true;
      if (t && t.done) heard += f.duration; else { allDone = false; if (t) heard += Math.min(t.t, f.duration); }
    }
    const ratio = total ? Math.min(1, heard / total) : 0;
    const state = allDone ? "done" : any ? "progress" : "new";
    row.dataset.state = state;
    row.querySelector(".fill").style.width = (ratio * 100).toFixed(1) + "%";
    row.querySelector(".left").textContent = state === "done" ? "聞き終わり" : state === "new" ? `${row.querySelector(".left").textContent} · 未再生` : left(total - heard);
    const pos = get("pos:" + id);
    if (pos && state === "progress" && (!latest || (pos.at || 0) > (latest.pos.at || 0))) latest = { row, pos, ratio, rest: total - heard };
  });
  if (latest) {
    const r = latest.row;
    document.getElementById("hero").hidden = false;
    document.getElementById("hero-link").href = `/books/${r.dataset.book}`;
    document.getElementById("hero-cover").replaceWith(r.querySelector(".cover-tpl").content.cloneNode(true));
    document.getElementById("hero-book").textContent = r.querySelector(".row-title").textContent;
    document.getElementById("hero-chapter").textContent = latest.pos.title || "";
    document.getElementById("hero-bar").style.width = (latest.ratio * 100).toFixed(1) + "%";
    document.getElementById("hero-left").textContent = left(latest.rest);
  }
  document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((c) => { c.classList.toggle("on", c === chip); c.setAttribute("aria-selected", c === chip); });
    const f = chip.dataset.filter;
    document.querySelectorAll(".title-row").forEach((row) => { row.hidden = f !== "all" && row.dataset.state !== f; });
  }));
})();
