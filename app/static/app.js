(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("form"), urlEl = $("url"), go = $("go"), statusEl = $("status");
  const preview = $("preview"), qualityRow = $("quality-row"), quality = $("quality");
  const progress = $("progress"), fill = $("bar-fill");

  const LABELS = { mp3: "Télécharger en MP3", mp4: "Télécharger en MP4", short: "Créer le Short" };
  let infoTimer = null, lastInfoUrl = "", busy = false;

  const fmt = () => form.querySelector('input[name="format"]:checked').value;

  function setStatus(text, kind = "") {
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.textContent = text;
  }

  function duration(s) {
    if (!s) return "";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    const mm = h ? String(m).padStart(2, "0") : m;
    return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
  }

  function syncFormat() {
    const f = fmt();
    qualityRow.hidden = f !== "mp4";
    go.textContent = LABELS[f];
  }

  async function loadInfo() {
    const url = urlEl.value.trim();
    if (!url || url === lastInfoUrl) return;
    lastInfoUrl = url;
    setStatus("Lecture du lien…");
    try {
      const res = await fetch("/api/info?url=" + encodeURIComponent(url));
      const data = await res.json();
      if (url !== urlEl.value.trim()) return;            // user typed something else
      if (!res.ok) throw new Error(data.error || data.detail || "Lien illisible.");
      $("thumb").src = data.thumbnail || "";
      $("title").textContent = data.title || "Sans titre";
      $("meta").textContent = [data.channel, duration(data.duration)].filter(Boolean).join(", ");
      preview.hidden = false;
      for (const opt of quality.options) opt.disabled = !data.qualities.includes(+opt.value);
      if (quality.selectedOptions[0].disabled) {
        quality.value = String(data.qualities[data.qualities.length - 1]);
      }
      const tooLong = data.duration && data.duration > data.max_duration;
      setStatus(tooLong ? "Vidéo trop longue pour MP3/MP4 ; le format Short reste possible." : "");
    } catch (e) {
      preview.hidden = true;
      setStatus(e.message, "error");
    }
  }

  function scheduleInfo(delay = 600) {
    clearTimeout(infoTimer);
    infoTimer = setTimeout(loadInfo, delay);
  }

  function showProgress(pct) {
    progress.hidden = false;
    progress.classList.toggle("indeterminate", pct == null);
    fill.style.width = pct == null ? "" : pct + "%";
  }

  function finish() {
    busy = false;
    go.disabled = false;
    syncFormat();
  }

  async function poll(id) {
    try {
      const res = await fetch("/api/jobs/" + id);
      const job = await res.json();
      if (!res.ok) throw new Error(job.detail || "Tâche introuvable.");
      if (job.status === "error") throw new Error(job.message);
      if (job.status === "done") {
        showProgress(100);
        const href = "/api/jobs/" + id + "/file";
        statusEl.className = "status";
        statusEl.innerHTML = "";
        statusEl.append("Fichier prêt : ");
        const a = Object.assign(document.createElement("a"), { href, textContent: job.filename });
        statusEl.append(a);
        a.click();
        finish();
        return;
      }
      const waiting = job.status === "queued";
      showProgress(job.status === "processing" || !job.progress ? null : job.progress);
      setStatus(waiting ? "En file d'attente…" : (job.message || "Téléchargement…") +
        (job.progress && job.status === "downloading" ? " " + Math.round(job.progress) + " %" : ""));
      setTimeout(() => poll(id), 1000);
    } catch (e) {
      progress.hidden = true;
      setStatus(e.message, "error");
      finish();
    }
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (busy) return;
    if (!urlEl.value.trim()) { setStatus("Collez d'abord un lien.", "error"); urlEl.focus(); return; }
    busy = true;
    go.disabled = true;
    go.textContent = "Préparation…";
    showProgress(null);
    setStatus("Envoi de la demande…");
    try {
      const body = new FormData(form);
      const res = await fetch("/api/jobs", { method: "POST", body });
      const job = await res.json();
      if (!res.ok) throw new Error(job.error || job.detail || "Demande refusée.");
      poll(job.id);
    } catch (e) {
      progress.hidden = true;
      setStatus(e.message, "error");
      finish();
    }
  });

  urlEl.addEventListener("input", () => scheduleInfo());
  urlEl.addEventListener("paste", () => scheduleInfo(50));
  form.querySelectorAll('input[name="format"]').forEach((r) => r.addEventListener("change", syncFormat));

  $("paste").addEventListener("click", async () => {
    try {
      urlEl.value = (await navigator.clipboard.readText()).trim();
      scheduleInfo(0);
    } catch {
      urlEl.focus();
      setStatus("Collez le lien avec Ctrl+V (ou appui long sur mobile).");
    }
  });

  // Support links like /?url=https://youtu.be/xxxx
  const shared = new URLSearchParams(location.search).get("url");
  if (shared) { urlEl.value = shared; scheduleInfo(0); }
  syncFormat();
})();
