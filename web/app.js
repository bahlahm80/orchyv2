const jobsEl = document.getElementById("jobs");
const metersEl = document.getElementById("meters");
const eventsEl = document.getElementById("events");
const chatLog = document.getElementById("chatLog");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const searchNow = document.getElementById("searchNow");

function meter(label, value) {
  return `<div><dt>${label}</dt><dd>${value}</dd></div>`;
}

function jobCard(row) {
  const canDecide = row.status === "pending_approval" && row.application_id;
  const actions = canDecide
    ? `<div class="actions">
         <span class="status">${row.status}</span>
         <button data-approve="${row.application_id}">Approve apply</button>
         <button class="danger" data-reject="${row.application_id}">Disapprove</button>
       </div>`
    : `<div class="actions"><span class="status">${row.status}</span>
         ${row.url ? `<a class="chip ghost" href="${row.url}" target="_blank" rel="noreferrer">Open listing</a>` : ""}
       </div>`;
  return `<article class="job">
    <div>
      <h3>${escapeHtml(row.title)}</h3>
      <p class="meta">${escapeHtml(row.company)} · ${escapeHtml(row.location)} · score ${Number(row.match_score).toFixed(2)}</p>
      <p class="reason">${escapeHtml(row.match_reason || "")}</p>
    </div>
    ${actions}
  </article>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderChat(messages) {
  chatLog.innerHTML = (messages || [])
    .map((m) => `<div class="bubble ${m.role}">${escapeHtml(m.content)}</div>`)
    .join("");
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function refresh() {
  const state = await (await fetch("/api/state")).json();
  const pending = (state.jobs || []).filter((j) => j.status === "pending_approval").length;
  metersEl.innerHTML = [
    meter("In progress", `${state.in_progress}/${state.max_in_progress}`),
    meter("Awaiting you", pending),
    meter("Gemini", state.has_gemini_key ? state.model : "no key"),
  ].join("");
  jobsEl.innerHTML = (state.jobs || []).map(jobCard).join("") || "<p class='meta'>No jobs yet. The search worker is cycling.</p>";
  eventsEl.innerHTML = (state.events || [])
    .map((e) => `<li><strong>${escapeHtml(e.actor)}</strong>${escapeHtml(e.message)}</li>`)
    .join("");
  renderChat(state.chat);
}

jobsEl.addEventListener("click", async (event) => {
  const approve = event.target.dataset.approve;
  const reject = event.target.dataset.reject;
  if (approve) await fetch(`/api/applications/${approve}/approve`, { method: "POST" });
  if (reject) await fetch(`/api/applications/${reject}/reject`, { method: "POST" });
  if (approve || reject) await refresh();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  chatInput.value = "";
  chatLog.insertAdjacentHTML("beforeend", `<div class="bubble user">${escapeHtml(message)}</div>`);
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const data = await res.json();
  chatLog.insertAdjacentHTML("beforeend", `<div class="bubble assistant">${escapeHtml(data.reply)}</div>`);
  chatLog.scrollTop = chatLog.scrollHeight;
});

searchNow.addEventListener("click", async () => {
  searchNow.disabled = true;
  await fetch("/api/search/now", { method: "POST" });
  searchNow.disabled = false;
  await refresh();
});

refresh();
setInterval(refresh, 4000);
