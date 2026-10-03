(() => {
  let sessionActive = false;
  let totalEnrolled = 0;
  let enrolledStudents = []; // [{ student_id, name }]

  // ---------- live video (MJPEG); reconnect if the stream drops
  const feed = $("feed");
  function loadFeed() { feed.src = "/api/cctv/video_feed?t=" + Date.now(); }
  feed.onerror = () => setTimeout(loadFeed, 5000);
  loadFeed();

  // ---------- CCTV status
  async function refreshCCTV() {
    const { ok, data } = await api("/api/cctv/status");
    const state = $("cctvState"), badge = $("feedBadge");
    if (!ok) { state.textContent = "Unknown"; state.className = "stat-value muted"; return; }
    $("cctvIp").textContent = "IP: " + (data.camera_ip || "not set");
    if (data.connected) {
      state.textContent = "Connected"; state.className = "stat-value good";
      badge.textContent = "Live"; badge.className = "badge live";
    } else {
      state.textContent = "Disconnected"; state.className = "stat-value bad";
      badge.textContent = "Disconnected"; badge.className = "badge off";
    }
  }

  // ---------- absent students (names)
  function renderAbsent(presence) {
    const list = $("absentList");
    const total = $("absentTotal");

    if (!sessionActive) {
      $("absentCount").textContent = 0;
      $("absentCount").className = "stat-value bad";
      if (total) total.textContent = "";
      if (list) list.innerHTML = '<li class="empty">Start a session to see absent students.</li>';
      return;
    }

    // Anyone with a presence record (Present or away) is not absent
    const seen = new Set((presence || []).map((p) => String(p.student_id)));
    const absentStudents = enrolledStudents.filter((s) => !seen.has(String(s.student_id)));

    $("absentCount").textContent = absentStudents.length;
    $("absentCount").className = "stat-value " + (absentStudents.length > 0 ? "bad" : "good");
    if (total) total.textContent = `(${absentStudents.length})`;

    if (list) {
      list.innerHTML = absentStudents.length
        ? absentStudents.map((s) => `<li>${esc(s.student_id)} - ${esc(s.name)}</li>`).join("")
        : '<li class="empty">Everyone is present.</li>';
    }
  }

  // ---------- session + attendance log
  async function refreshSession() {
    const { ok, data } = await api("/api/session/status");
    if (!ok) return;
    sessionActive = !!data.active;
    $("sessState").textContent = sessionActive ? "Active" : "Inactive";
    $("sessState").className = "stat-value " + (sessionActive ? "good" : "bad");
    $("sessSub").textContent = sessionActive ? data.session.subject : "No active class";

    $("presentCount").textContent = data.present_count || 0;

    renderAbsent(data.presence);

    $("endBtn").hidden = !sessionActive;

    const body = $("logBody");
    if (!sessionActive) {
      body.innerHTML = '<tr><td colspan="4" class="empty">No session active. Enter a subject and start a session to begin recording.</td></tr>';
    } else if (!data.presence.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty">Session running. Waiting for students to be recognised…</td></tr>';
    } else {
      body.innerHTML = data.presence.map((p) =>
        `<tr><td>${esc(p.student_id)}</td><td>${esc(p.name)}</td><td>${esc(p.entry_time)}</td>` +
        `<td><span class="pill ${p.status === "Present" ? "present" : "away"}">${esc(p.status)}</span></td></tr>`).join("");
    }
  }

  // ---------- enrolled students list + count
  async function refreshStats() {
    const { ok, data } = await api("/api/students");
    if (ok) {
      enrolledStudents = Array.isArray(data) ? data : [];
      totalEnrolled = enrolledStudents.length;
      $("enrolled").textContent = totalEnrolled;
    }
  }

  // ---------- Start: ALWAYS a fresh session
  $("startBtn").addEventListener("click", async () => {
    const msg = $("sessMsg"), btn = $("startBtn");
    const subject = $("subject").value.trim();
    if (!subject) { showMsg(msg, "Enter a subject name first.", "error"); return; }
    if (sessionActive && !confirm("A session is running. Starting a new one will end it and save its attendance. Continue?")) return;

    btn.disabled = true; btn.textContent = "Starting…";
    const r = await postJSON("/api/session/start", { subject });
    btn.disabled = false; btn.textContent = "Start new session";
    if (r.ok) showMsg(msg, `New session started: ${subject}`, "success");
    else showMsg(msg, r.data.message || "Could not start the session.", "error");
    refreshSession();
  });

  $("endBtn").addEventListener("click", async () => {
    if (!confirm("End this session and save attendance?")) return;
    const r = await postJSON("/api/session/end");
    showMsg($("sessMsg"), r.data.message || "Done.", r.ok ? "success" : "error");
    refreshSession();
  });

  // ---------- add student modal
  const modal = $("modal"), form = $("addForm");
  $("openAdd").onclick = () => { modal.hidden = false; };
  $("closeAdd").onclick = () => { modal.hidden = true; };
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("addMsg"), btn = form.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Saving…";
    const r = await api("/api/students/register", { method: "POST", body: new FormData(form) });
    btn.disabled = false; btn.textContent = "Save student";
    let text = r.data.message || "Something went wrong.";
    if (r.ok && r.data.rejected && r.data.rejected.length) text += " Skipped: " + r.data.rejected.join("; ");
    showMsg(msg, text, r.ok ? "success" : "error");
    if (r.ok) { form.reset(); refreshStats().then(refreshSession); }
  });

  // Load the student list first so the first render of "absent" is correct
  refreshCCTV();
  refreshStats().then(refreshSession);
  setInterval(refreshCCTV, 3000);
  setInterval(refreshSession, 2000);
  setInterval(refreshStats, 15000);
})();