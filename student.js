(async () => {
  const { ok, data } = await api("/api/student/my_attendance");
  const body = $("myBody");
  if (!ok) { body.innerHTML = '<tr><td colspan="6" class="empty">Could not load your attendance.</td></tr>'; return; }
  $("pct").textContent = data.percent + "%";
  $("att").textContent = data.attended;
  $("tot").textContent = data.total_sessions;
  body.innerHTML = data.records.length ? data.records.map((r) =>
    `<tr><td>${esc(r.date)}</td><td>${esc(r.subject)}</td><td>${esc(r.entry_time)}</td>` +
    `<td>${esc(r.exit_time || "–")}</td><td>${esc(r.duration_minutes)}</td><td>${esc(r.status)}</td></tr>`).join("")
    : '<tr><td colspan="6" class="empty">No attendance recorded yet.</td></tr>';
})();
