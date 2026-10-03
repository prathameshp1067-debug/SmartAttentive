(() => {
  // ---------- tabs
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    ["teachers", "students", "attendance"].forEach((n) => { $("tab-" + n).hidden = n !== t.dataset.tab; });
    if (t.dataset.tab === "students") loadStudents();
    if (t.dataset.tab === "attendance") { loadSessions(); loadAttendance(); }
  }));

  async function loadStats() {
    const { ok, data } = await api("/api/stats");
    if (!ok) return;
    $("sStudents").textContent = data.total_enrolled;
    $("sTeachers").textContent = data.total_teachers;
    $("sSessions").textContent = data.total_sessions;
  }

  // ---------- teachers
  async function loadTeachers() {
    const { data } = await api("/api/teachers");
    const rows = Array.isArray(data) ? data : [];
    $("teacherBody").innerHTML = rows.length ? rows.map((t) =>
      `<tr><td>${esc(t.username)}</td><td>${esc(t.name)}</td><td>${esc(t.email || "")}</td>` +
      `<td><button class="btn btn-danger btn-sm" data-del="${esc(t.username)}">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">No teachers yet. Add one above.</td></tr>';
  }
  $("teacherBody").addEventListener("click", async (e) => {
    const u = e.target.dataset.del;
    if (u && confirm(`Delete teacher ${u}?`)) { await api("/api/teachers/" + encodeURIComponent(u), { method: "DELETE" }); loadTeachers(); loadStats(); }
  });
  $("teacherForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = Object.fromEntries(new FormData(f));
    const r = await postJSON("/api/teachers/add", body);
    showMsg($("teacherMsg"), r.data.message || "Failed.", r.ok ? "success" : "error");
    if (r.ok) { f.reset(); loadTeachers(); loadStats(); }
  });

  // ---------- students
  async function loadStudents() {
    const { data } = await api("/api/students");
    const rows = Array.isArray(data) ? data : [];
    $("studentBody").innerHTML = rows.length ? rows.map((s) =>
      `<tr><td>${esc(s.student_id)}</td><td>${esc(s.name)}</td><td>${esc(s.department)}</td><td>${esc(s.year)}</td>` +
      `<td>${esc(s.division)}</td><td>${esc(s.roll_number)}</td><td>${esc(s.photo_count || 0)}</td>` +
      `<td><button class="btn btn-danger btn-sm" data-del="${esc(s.student_id)}">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="8" class="empty">No students yet. Teachers add them from the teacher dashboard.</td></tr>';
  }
  $("studentBody").addEventListener("click", async (e) => {
    const id = e.target.dataset.del;
    if (id && confirm(`Delete student ${id}? Their past attendance records are kept.`)) {
      await api("/api/students/" + encodeURIComponent(id), { method: "DELETE" }); loadStudents(); loadStats();
    }
  });

  // ---------- attendance
  async function loadSessions() {
    const { data } = await api("/api/sessions");
    const sel = $("fSession"), cur = sel.value;
    sel.innerHTML = '<option value="">All sessions</option>' + (Array.isArray(data) ? data : []).map((s) =>
      `<option value="${esc(s.session_id)}">${esc(s.date)} · ${esc(s.subject)}</option>`).join("");
    sel.value = cur;
  }
  function filterQuery() {
    const q = new URLSearchParams();
    if ($("fSession").value) q.set("session_id", $("fSession").value);
    if ($("fDate").value) q.set("date", $("fDate").value);
    if ($("fStudent").value.trim()) q.set("student_id", $("fStudent").value.trim());
    return q.toString();
  }
  async function loadAttendance() {
    const q = filterQuery();
    $("exportLink").href = "/api/attendance/export" + (q ? "?" + q : "");
    const { data } = await api("/api/attendance" + (q ? "?" + q : ""));
    const rows = Array.isArray(data) ? data : [];
    $("attBody").innerHTML = rows.length ? rows.map((r) =>
      `<tr><td>${esc(r.date)}</td><td>${esc(r.subject)}</td><td>${esc(r.student_id)}</td><td>${esc(r.student_name)}</td>` +
      `<td>${esc(r.entry_time)}</td><td>${esc(r.exit_time || "–")}</td><td>${esc(r.duration_minutes)}</td></tr>`).join("")
      : '<tr><td colspan="7" class="empty">No attendance records match these filters.</td></tr>';
  }
  $("applyFilter").addEventListener("click", loadAttendance);

  loadStats(); loadTeachers();
})();
