"use strict";
const byId = (id) => document.getElementById(id);
let catalog, selected, pollTimer, dirty = false, planRevision;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function link(url, text) {
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) return node("span", text);
  } catch { return node("span", text); }
  const a = node("a", text);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
function message(text, error = false) {
  byId("message").textContent = text;
  byId("message").className = error ? "error" : "";
}
async function api(path, data) {
  const response = await fetch(`/api/${path}`, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Review-Token": catalog.token},
    body: JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || "Request failed");
    error.duplicateUrl = body.duplicate_url;
    throw error;
  }
  return body;
}
function health(feed) {
  if (["ok", "unchanged"].includes(feed.status)) return "healthy";
  if (["failed", "stale"].includes(feed.status)) return "failed";
  return "unchecked";
}
function activity(feed) {
  if (!feed.latest_timestamp) return "unknown";
  const days = (Date.now() / 1000 - feed.latest_timestamp) / 86400;
  if (days < -1) return "future dated";
  if (days > 180) return "quiet";
  if (days <= 30) return "recent";
  return "older";
}
function matches(feed) {
  const query = byId("review-search").value.trim().toLocaleLowerCase();
  const text = [feed.title, feed.feed_title, feed.url, feed.publisher, feed.description,
    ...feed.tags, feed.decision.title || "", ...(feed.decision.tags || []), feed.decision.notes || ""].join(" ").toLocaleLowerCase();
  const h = byId("health").value, category = byId("review-category").value;
  return text.includes(query) && (!h || h === health(feed) ||
    (h === "unidentified" && !feed.feed_title) || (h === "duplicate" && feed.lines.length > 1)) &&
    (!category || (category === "__none__" ? !feed.tags.length : feed.tags.includes(category))) &&
    (!byId("decision-filter").value || byId("decision-filter").value === (feed.decision.action || "unreviewed")) &&
    (!byId("activity").value || byId("activity").value === activity(feed));
}
function renderList() {
  const list = byId("source-list");
  const scroll = list.scrollTop;
  list.replaceChildren();
  const shown = catalog.feeds.filter(matches);
  byId("shown").textContent = `${shown.length} of ${catalog.feeds.length} sources`;
  for (const feed of shown) {
    const button = node("button", undefined, "source-choice");
    button.type = "button"; button.dataset.url = feed.url;
    button.setAttribute("aria-pressed", String(feed.url === selected));
    button.append(node("strong", feed.title), node("small", feed.domain));
    button.append(node("span", feed.checking ? "Checking…" : health(feed) === "healthy" ? "Fetch OK" : health(feed) === "failed" ? "Fetch failed" : "Needs check", `badge ${health(feed)}`));
    button.append(node("span", feed.decision.action || "unreviewed", "badge"));
    if (feed.lines.length > 1) button.append(node("span", `${feed.lines.length} occurrences`, "badge"));
    button.append(node("small", feed.tags.join(" / ") || "Uncategorized"));
    button.addEventListener("click", () => {
      if (dirty) { message("Save the current review before selecting another source.", true); return; }
      selected = feed.url; renderList(); renderDetail();
    });
    list.append(button);
  }
  list.scrollTop = scroll;
}
function input(label, id, value, tag = "input") {
  const wrapper = node("div"), caption = node("label", label), control = node(tag);
  caption.htmlFor = id; control.id = id; control.value = value;
  control.addEventListener("input", () => { dirty = true; const discard = byId("discard-draft"); if (discard) discard.disabled = false; });
  wrapper.append(caption, control); return wrapper;
}
function renderDetail() {
  const feed = catalog.feeds.find((f) => f.url === selected);
  const detail = byId("detail"); detail.replaceChildren();
  if (!feed) { detail.append(node("p", "Select a source to review it.")); return; }
  detail.append(node("h2", feed.title), link(feed.url, feed.url));
  detail.lastChild.className = "feed-url";
  if (feed.description) detail.append(node("p", feed.description));
  if (feed.homepage) { const home = node("p"); home.append(link(feed.homepage, "Publisher website ↗")); detail.append(home); }
  const facts = node("dl", undefined, "facts");
  const entries = [
    ["Feed title", feed.feed_title || "Not identified yet"],
    ["Declared author", feed.publisher || "Not supplied by feed"],
    ["Current categories", feed.tags.join(", ") || "None"],
    ["Source lines", feed.lines.join(", ")],
    ["Last checked", feed.checked_at || "No live check recorded"],
    ["Last content fetch", feed.fetched_at || "Not recorded"],
    ["Fetch result", `${feed.status || "unchecked"}${feed.http_status ? ` · HTTP ${feed.http_status}` : ""}${feed.elapsed_seconds !== undefined && feed.elapsed_seconds !== null ? ` · ${feed.elapsed_seconds}s` : ""}`],
    ["Latest article", feed.latest_timestamp ? `${new Date(feed.latest_timestamp * 1000).toISOString().slice(0, 10)} · ${activity(feed)}` : "Date unknown"],
    ["Feed format", [feed.format, feed.language].filter(Boolean).join(" · ") || "Not collected yet"],
  ];
  if (feed.final_url && feed.final_url !== feed.url) entries.push(["Final URL", feed.final_url]);
  if (feed.content_type) entries.push(["Content type", feed.content_type]);
  for (const [label, value] of entries) facts.append(node("dt", label), node("dd", value));
  detail.append(facts);
  if (feed.error) detail.append(node("p", feed.error, "notice"));
  if (feed.parser_warning) detail.append(node("p", `Parser warning: ${feed.parser_warning}`, "muted"));
  const recheck = node("button", feed.checking ? "Checking…" : "Check this feed now");
  recheck.id = "check-one"; recheck.disabled = feed.checking;
  recheck.addEventListener("click", () => run(async () => { await api("check", {url: feed.url}); await refresh(); }));
  detail.append(recheck, node("h3", "Recent article samples"));
  const articles = node("ul");
  for (const article of feed.articles || []) {
    const li = node("li"); li.append(link(article.link, article.title));
    if (article.published) li.append(node("span", ` · ${article.published}`, "muted"));
    articles.append(li);
  }
  detail.append(articles.childElementCount ? articles : node("p", "No saved articles available.", "muted"));
  if (feed.history?.length) {
    const history = node("details"), summary = node("summary", "Recent checks"), list = node("ul");
    for (const check of [...feed.history].reverse()) list.append(node("li", `${check.checked_at} · ${check.status}${check.http_status ? ` · HTTP ${check.http_status}` : ""}${check.error ? ` · ${check.error}` : ""}`));
    history.append(summary, list); detail.append(history);
  }
  detail.append(node("h3", "Your review"));
  const grid = node("div", undefined, "edit-grid");
  grid.append(input("Display name", "edit-title", feed.decision.title ?? (feed.override || feed.feed_title || "")));
  grid.append(input("Categories (comma separated)", "edit-tags", (feed.decision.tags || feed.tags).join(", ")));
  detail.append(grid, input("Notes / reason", "edit-notes", feed.decision.notes || "", "textarea"));
  const actions = node("div", undefined, "toolbar");
  for (const [action, label] of [["keep", "Keep with these names / tags"], ["investigate", "Investigate"], ["retire", "Retire"], ["unreviewed", "Reset review"]]) {
    const button = node("button", label, action === "keep" ? "primary" : "");
    button.dataset.action = action;
    button.addEventListener("click", () => run(async () => {
      await api("decision", {url: feed.url, decision: {action, title: byId("edit-title").value,
        tags: byId("edit-tags").value.split(",").map((x) => x.trim()).filter(Boolean), notes: byId("edit-notes").value}});
      dirty = false; message(`Saved ${action} decision. Preview changes when ready.`); await refresh();
    }));
    actions.append(button);
  }
  const discard = node("button", "Discard unsaved edits");
  discard.id = "discard-draft"; discard.disabled = true;
  discard.addEventListener("click", () => { dirty = false; renderDetail(); message("Unsaved edits discarded."); });
  actions.append(discard);
  detail.append(actions, node("p", "Saving a review does not edit urls. Keep applies the display name and tags; Retire comments out the source when you apply the preview.", "muted"));
}
async function refresh() {
  const data = await api("feeds"); catalog = data;
  byId("add-feed").disabled = false;
  const category = byId("review-category"), current = category.value;
  category.replaceChildren(new Option("All categories", ""), new Option("Uncategorized", "__none__"), ...data.categories.map((tag) => new Option(tag, tag)));
  category.value = current;
  if (!selected || !data.feeds.some((feed) => feed.url === selected)) selected = data.feeds[0]?.url;
  const healthy = data.feeds.filter((f) => health(f) === "healthy").length;
  const failures = data.feeds.filter((f) => health(f) === "failed").length;
  const reviewed = data.feeds.filter((f) => f.decision.action && f.decision.action !== "unreviewed").length;
  byId("summary").textContent = `${data.feeds.length} sources · ${healthy} fetch OK · ${failures} fetch failed · ${reviewed} reviewed${data.pending ? ` · ${data.pending} checks remaining` : ""}`;
  byId("check-all").disabled = data.pending > 0;
  renderList(); if (!dirty) renderDetail();
  clearTimeout(pollTimer);
  if (data.pending) pollTimer = setTimeout(() => run(refresh), 2000);
  if (Object.keys(data.scan_errors).length) message("Some checks could not complete: " + Object.values(data.scan_errors).join("; "), true);
}
async function run(task) { try { await task(); } catch (error) { message(error.message, true); } }
for (const id of ["review-search", "health", "review-category", "decision-filter", "activity"]) {
  byId(id).addEventListener(id === "review-search" ? "input" : "change", renderList);
}
byId("check-all").addEventListener("click", () => run(async () => { await api("check", {}); message("Checking the collection. You can continue reviewing while checks run."); await refresh(); }));
byId("next-review").addEventListener("click", () => {
  if (dirty) { message("Save the current review before moving to the next source.", true); return; }
  const index = catalog.feeds.findIndex((feed) => feed.url === selected);
  const ordered = [...catalog.feeds.slice(index + 1), ...catalog.feeds.slice(0, index + 1)];
  const next = ordered.find((feed) => matches(feed) && (!feed.decision.action || feed.decision.action === "unreviewed"));
  if (!next) { message("No unreviewed sources match the current filters."); return; }
  selected = next.url; renderList(); renderDetail();
  [...byId("source-list").children].find((button) => button.dataset.url === selected)?.scrollIntoView({block: "nearest"});
});
byId("export").addEventListener("click", () => run(async () => {
  const decisions = await api("export"), a = node("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(decisions, null, 2)], {type: "application/json"}));
  a.download = "feed-decisions.json"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}));
byId("export-catalog").addEventListener("click", () => {
  const a = node("a"); a.href = "/api/catalog.csv"; a.download = "feed-catalog.csv"; a.click();
});
byId("preview").addEventListener("click", () => run(async () => {
  if (dirty) throw new Error("Save the current review before previewing changes.");
  const plan = await api("plan"); planRevision = plan.revision;
  byId("plan-diff").textContent = plan.diff || "No accepted changes to apply.";
  byId("apply-plan").disabled = !plan.changed_lines;
  byId("plan-dialog").showModal();
}));
byId("close-plan").addEventListener("click", () => byId("plan-dialog").close());
byId("apply-plan").addEventListener("click", () => run(async () => {
  const result = await api("apply", {revision: planRevision});
  byId("plan-dialog").close();
  message(`Updated ${result.changed_lines} source lines. Backup: ${result.backup || "no changes"}. Rebuild the reader images / static site to use the new collection.`);
  await refresh();
}));
window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

let addGeneration = 0, addEditVersion = 0, addSaving = false, addToken, addRevision, duplicateUrl;
function addMessage(text, error = false) {
  byId("add-message").textContent = text;
  byId("add-message").className = error ? "error" : "";
}
function invalidateAddition() {
  addEditVersion++;
  addRevision = undefined;
  byId("add-save").disabled = true;
  byId("add-diff").hidden = true;
}
function invalidateCheck() {
  addGeneration++; addToken = undefined; duplicateUrl = undefined;
  invalidateAddition();
  byId("add-result").hidden = true;
  byId("add-open-existing").hidden = true;
  addMessage("");
}
function showAddition(feed) {
  const panel = byId("add-identity"); panel.replaceChildren();
  panel.append(node("h3", feed.feed_title || "Feed title not supplied"));
  panel.append(node("p", `Validated ${feed.format || "RSS/Atom"} · HTTP ${feed.http_status}`));
  if (feed.publisher) panel.append(node("p", `Declared author: ${feed.publisher}`));
  if (feed.description) panel.append(node("p", feed.description));
  if (feed.homepage) panel.append(link(feed.homepage, "Publisher website ↗"));
  if (feed.final_url && feed.final_url !== feed.url) panel.append(node("p", `Redirects to ${feed.final_url}`, "muted"));
  const samples = node("ul");
  for (const article of feed.articles.slice(0, 3)) {
    const item = node("li"); item.append(link(article.link, article.title)); samples.append(item);
  }
  panel.append(samples.childElementCount ? samples : node("p", "This feed has no articles yet.", "muted"));
  byId("add-name").value = (feed.feed_title || "").slice(0, 500);
  byId("add-result").hidden = false;
}
byId("add-feed").addEventListener("click", () => {
  if (dirty) { message("Save or discard the current review before adding a feed.", true); return; }
  invalidateCheck(); byId("add-check-form").reset();
  for (const id of ["add-name", "add-tags", "add-notes"]) byId(id).value = "";
  byId("add-url").disabled = false; byId("add-check").disabled = false;
  byId("add-tag-options").replaceChildren(...catalog.categories.map((tag) => new Option(tag, tag)));
  byId("add-dialog").showModal(); byId("add-url").focus();
});
byId("add-close").addEventListener("click", () => byId("add-dialog").close());
byId("add-dialog").addEventListener("close", invalidateCheck);
byId("add-dialog").addEventListener("cancel", (event) => { if (addSaving) event.preventDefault(); });
byId("add-url").addEventListener("input", invalidateCheck);
for (const id of ["add-name", "add-tags", "add-notes"]) byId(id).addEventListener("input", invalidateAddition);
byId("add-check-form").addEventListener("submit", async (event) => {
  event.preventDefault(); invalidateCheck();
  const generation = addGeneration;
  byId("add-check").disabled = true; byId("add-url").disabled = true;
  addMessage("Checking the feed…");
  try {
    const result = await api("add/check", {url: byId("add-url").value});
    if (generation !== addGeneration) return;
    addToken = result.check_token; byId("add-url").value = result.feed.url;
    showAddition(result.feed); addMessage("Feed identified. Choose its name and categories, then preview the addition.");
  } catch (error) {
    if (generation !== addGeneration) return;
    addMessage(error.message, true); duplicateUrl = error.duplicateUrl;
    byId("add-open-existing").hidden = !duplicateUrl;
  } finally {
    if (generation === addGeneration) { byId("add-check").disabled = false; byId("add-url").disabled = false; }
  }
});
byId("add-open-existing").addEventListener("click", async () => {
  selected = duplicateUrl; byId("add-dialog").close();
  for (const id of ["review-search", "health", "review-category", "decision-filter", "activity"]) byId(id).value = "";
  await run(refresh);
});
byId("add-preview").addEventListener("click", async () => {
  const generation = addGeneration, editVersion = addEditVersion;
  byId("add-preview").disabled = true;
  try {
    const result = await api("add/preview", {check_token: addToken, decision: {
      title: byId("add-name").value, tags: byId("add-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean),
      notes: byId("add-notes").value,
    }});
    if (generation !== addGeneration || editVersion !== addEditVersion) return;
    addRevision = result.revision; byId("add-diff").textContent = result.diff;
    byId("add-diff").hidden = false; byId("add-save").disabled = false;
    addMessage("Review the diff below, then save the feed. Existing review decisions are applied separately.");
  } catch (error) { if (generation === addGeneration) addMessage(error.message, true); }
  finally { byId("add-preview").disabled = false; }
});
byId("add-save").addEventListener("click", async () => {
  addSaving = true;
  byId("add-save").disabled = true;
  const controls = ["add-close", "add-url", "add-check", "add-preview", "add-name", "add-tags", "add-notes"];
  for (const id of controls) byId(id).disabled = true;
  try {
    const result = await api("add/save", {check_token: addToken, revision: addRevision});
    byId("add-dialog").close(); selected = result.url;
    for (const id of ["review-search", "health", "review-category", "decision-filter", "activity"]) byId(id).value = "";
    await refresh();
    [...byId("source-list").children].find((button) => button.dataset.url === selected)?.scrollIntoView({block: "nearest"});
    message(`Added feed. Backup: ${result.backup}. Rebuild the reader images / static site to use it.`);
  } catch (error) { invalidateAddition(); addMessage(error.message, true); }
  finally { addSaving = false; for (const id of controls) byId(id).disabled = false; }
});
run(refresh);
