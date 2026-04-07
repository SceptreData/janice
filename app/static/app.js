// -- State --
let chatHistory = [];
let pendingFiles = [];
let isStreaming = false;
let selectedModel = localStorage.getItem("model") || "";

function startStreaming() {
  isStreaming = true;
  document.getElementById("send-btn").disabled = true;
}

function stopStreaming(msgEl) {
  isStreaming = false;
  document.getElementById("send-btn").disabled = false;
  removeStreamingIndicator(msgEl);
}

// -- Chat --

function handleKeyDown(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

async function streamChat(message, history, msgEl) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      model: selectedModel || undefined,
    }),
  });

  if (!res.ok) {
    const err = await res.json();
    appendToAssistant(msgEl, err.error || "Request failed");
    return null;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    let eventType = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7);
      } else if (line.startsWith("data: ")) {
        const data = line.slice(6);
        handleSSE(eventType, data, msgEl);
        if (eventType === "text") fullText = data;
      }
    }
  }

  return fullText;
}

async function sendMessage(override) {
  const input = document.getElementById("chat-input");
  const text = override || input.value.trim();
  if (!text || isStreaming) return;

  input.value = "";
  autoResizeInput();
  appendMessage("user", text);
  chatHistory.push({ role: "user", content: text });

  startStreaming();
  const msgEl = createAssistantMessage();

  try {
    const fullText = await streamChat(text, chatHistory.slice(0, -1), msgEl);
    if (fullText) {
      chatHistory.push({ role: "assistant", content: fullText });
    }
  } catch (err) {
    appendToAssistant(msgEl, `\n\nError: ${err.message}`, "error");
  }

  stopStreaming(msgEl);
}

function handleSSE(event, data, msgEl) {
  switch (event) {
    case "text":
      setAssistantText(msgEl, data);
      break;
    case "tool_call": {
      const tc = JSON.parse(data);
      appendToolCall(msgEl, tc.tool, tc.args);
      break;
    }
    case "tool_result": {
      const tr = JSON.parse(data);
      appendToolResult(msgEl, tr.tool, tr.result);
      break;
    }
    case "wiki_update":
      refreshWiki();
      break;
    case "done":
      refreshPendingFiles();
      break;
  }
}

function appendMessage(role, content) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerHTML = `<div class="role">${role}</div><div class="content">${escapeHtml(content)}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function createAssistantMessage() {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message assistant";
  div.innerHTML = `<div class="role">janice <span class="streaming-indicator"></span></div><div class="content"></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function setAssistantText(msgEl, text) {
  const contentEl = msgEl.querySelector(".content");
  // Preserve tool call/result elements already in the content
  let textNode = contentEl.querySelector(".assistant-text");
  if (!textNode) {
    textNode = document.createElement("div");
    textNode.className = "assistant-text";
    contentEl.prepend(textNode);
  }
  textNode.innerHTML = renderMarkdown(text);
  // Make wikilinks clickable
  textNode.querySelectorAll("a").forEach(bindWikiLink);
  const container = document.getElementById("chat-messages");
  container.scrollTop = container.scrollHeight;
}

function appendToAssistant(msgEl, text) {
  const contentEl = msgEl.querySelector(".content");
  contentEl.innerHTML += escapeHtml(text);
  const container = document.getElementById("chat-messages");
  container.scrollTop = container.scrollHeight;
}

function appendToolCall(msgEl, toolName, args) {
  const div = document.createElement("div");
  div.className = "tool-activity";
  const summary = Object.entries(args)
    .map(([k, v]) => {
      const val =
        typeof v === "string" && v.length > 40 ? v.slice(0, 40) + "..." : v;
      return typeof val === "string" ? val : JSON.stringify(val);
    })
    .join(", ");
  div.innerHTML = `<span class="tool-label">${escapeHtml(toolName)}</span> <span class="tool-summary">${escapeHtml(summary)}</span>`;
  // Store ref so the result can be appended as detail
  div.dataset.tool = toolName;
  msgEl.querySelector(".content").appendChild(div);
  const container = document.getElementById("chat-messages");
  container.scrollTop = container.scrollHeight;
}

function appendToolResult(msgEl, toolName, result) {
  // Find the matching tool-activity element and add a collapsible detail
  const activities = msgEl.querySelectorAll(".tool-activity");
  let target = null;
  for (const el of activities) {
    if (el.dataset.tool === toolName && !el.querySelector(".tool-detail")) {
      target = el;
      break;
    }
  }
  if (!target) return;

  const truncated =
    result.length > 300 ? result.slice(0, 300) + "\n..." : result;
  const detail = document.createElement("pre");
  detail.className = "tool-detail";
  detail.textContent = truncated;
  target.appendChild(detail);
  target.style.cursor = "pointer";
  target.onclick = () => target.classList.toggle("expanded");

  const container = document.getElementById("chat-messages");
  container.scrollTop = container.scrollHeight;
}

function removeStreamingIndicator(msgEl) {
  const indicator = msgEl.querySelector(".streaming-indicator");
  if (indicator) indicator.remove();
}

// -- Wiki Browser --

async function refreshWiki() {
  try {
    const res = await fetch("/api/wiki");
    const pages = await res.json();
    renderFileTree(pages);
  } catch (e) {
    // silent
  }
}

function renderFileTree(pages) {
  const tree = document.getElementById("file-tree");
  tree.innerHTML = "";
  for (const page of pages) {
    const item = document.createElement("div");
    item.className = "file-tree-item";
    item.innerHTML = `<span class="page-name">${escapeHtml(page.name)}</span><span class="page-summary">${escapeHtml(page.summary)}</span>`;
    item.onclick = () => openWikiPage(page.name);
    tree.appendChild(item);
  }
}

async function openWikiPage(name) {
  try {
    const res = await fetch(`/api/wiki/${encodeURIComponent(name)}`);
    const data = await res.json();
    if (data.error) return;

    const pageEl = document.getElementById("wiki-page");
    let html = "";

    // Frontmatter display
    if (data.frontmatter && data.frontmatter.title) {
      const fm = data.frontmatter;
      html += `<div class="frontmatter">`;
      if (fm.draft) {
        html += `<span class="draft-badge">DRAFT</span>`;
      }
      if (fm.tags && fm.tags.length) {
        html += fm.tags
          .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
          .join("");
      }
      if (fm.updated)
        html += ` <span>updated ${escapeHtml(fm.updated.toString())}</span>`;
      html += `</div>`;
    }

    html += renderMarkdown(data.body);
    html += `<div class="wiki-actions">`;
    html += `<button class="wiki-action-btn" onclick="analyzeWikiPage('${escapeHtml(name)}')">Dig deeper -- ask me questions</button>`;
    html += `</div>`;
    pageEl.innerHTML = html;

    // Make wikilinks clickable
    pageEl.querySelectorAll("a").forEach(bindWikiLink);

    switchTab("page");
  } catch (e) {
    // silent
  }
}

function bindWikiLink(a) {
  const href = a.getAttribute("href");
  if (href && !href.startsWith("http") && !href.startsWith("/")) {
    a.classList.add("wikilink");
    a.onclick = (e) => {
      e.preventDefault();
      openWikiPage(href);
    };
  }
}

// -- Graph View --

async function renderGraph() {
  try {
    const res = await fetch("/api/wiki/graph");
    const data = await res.json();
    drawGraph(data);
  } catch (e) {
    // silent
  }
}

const NODE_COLORS = {
  source: "--blue",
  entity: "--yellow",
  concept: "--purple",
  topic: "--aqua",
  meta: "--fg-dim",
};

function nodeColor(d) {
  return cssVar(NODE_COLORS[d.type] || "--aqua");
}

function drawGraph(data) {
  const container = document.getElementById("graph-container");
  container.innerHTML = "";

  const width = container.clientWidth || 600;
  const height = container.clientHeight || 400;

  const svg = d3
    .select(container)
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("viewBox", [0, 0, width, height]);

  if (!data.nodes.length) {
    svg
      .append("text")
      .attr("x", width / 2)
      .attr("y", height / 2)
      .attr("text-anchor", "middle")
      .attr("fill", cssVar("--fg-dim"))
      .text("No wiki pages yet");
    return;
  }

  const simulation = d3
    .forceSimulation(data.nodes)
    .force(
      "link",
      d3
        .forceLink(data.edges)
        .id((d) => d.id)
        .distance(80),
    )
    .force("charge", d3.forceManyBody().strength(-200))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(30));

  const link = svg
    .append("g")
    .selectAll("line")
    .data(data.edges)
    .join("line")
    .attr("stroke", cssVar("--bg2"))
    .attr("stroke-width", 1.5);

  const node = svg
    .append("g")
    .selectAll("g")
    .data(data.nodes)
    .join("g")
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        }),
    )
    .style("cursor", "pointer")
    .on("click", (event, d) => openWikiPage(d.id));

  node
    .append("circle")
    .attr("r", 8)
    .attr("fill", (d) => nodeColor(d))
    .attr("stroke", cssVar("--bg"))
    .attr("stroke-width", 2);

  node
    .append("text")
    .text((d) => d.title)
    .attr("dx", 12)
    .attr("dy", 4)
    .attr("fill", cssVar("--fg"))
    .attr("font-size", "12px");

  // Legend
  const legendTypes = [...new Set(data.nodes.map((n) => n.type))].sort();
  const legend = svg.append("g").attr("transform", "translate(12, 16)");
  legendTypes.forEach((type, i) => {
    const g = legend.append("g").attr("transform", `translate(0, ${i * 20})`);
    g.append("circle")
      .attr("r", 5)
      .attr("fill", cssVar(NODE_COLORS[type] || "--aqua"));
    g.append("text")
      .text(type)
      .attr("x", 12)
      .attr("dy", 4)
      .attr("fill", cssVar("--fg-dim"))
      .attr("font-size", "11px");
  });

  simulation.on("tick", () => {
    link
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });
}

// -- Tabs --

function switchTab(tab) {
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  document
    .querySelectorAll(".tab-content")
    .forEach((c) => c.classList.toggle("active", c.id === `tab-${tab}`));

  if (tab === "graph") renderGraph();
  if (tab === "tree") refreshWiki();
}

// -- Drag and Drop --

function setupDragDrop() {
  const pane = document.getElementById("pane-left");
  const overlay = document.getElementById("drop-overlay");
  let dragCounter = 0;

  pane.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    overlay.classList.add("visible");
  });

  pane.addEventListener("dragleave", () => {
    dragCounter--;
    if (dragCounter === 0) overlay.classList.remove("visible");
  });

  pane.addEventListener("dragover", (e) => e.preventDefault());

  pane.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragCounter = 0;
    overlay.classList.remove("visible");

    const files = Array.from(e.dataTransfer.files);
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      try {
        await fetch("/api/sources", { method: "POST", body: form });
        if (!pendingFiles.includes(file.name)) {
          pendingFiles.push(file.name);
        }
      } catch (err) {
        // silent
      }
    }

    updateIngestBanner();
  });
}

function updateIngestBanner() {
  const banner = document.getElementById("ingest-banner");
  if (pendingFiles.length) {
    document.getElementById("ingest-count").textContent =
      `${pendingFiles.length} file${pendingFiles.length === 1 ? "" : "s"} waiting to be ingested`;
    banner.classList.add("visible");
  } else {
    banner.classList.remove("visible");
  }
}

function ingestFiles() {
  const names = pendingFiles.join(", ");
  pendingFiles = [];
  updateIngestBanner();
  sendMessage(`Ingest these new source files: ${names}`);
}

async function refreshPendingFiles() {
  try {
    const res = await fetch("/api/sources/pending");
    pendingFiles = await res.json();
    updateIngestBanner();
  } catch (e) {
    // ignore
  }
}

// -- Resize Handle --

function setupResize() {
  const handle = document.getElementById("resize-handle");
  const left = document.getElementById("pane-left");
  let startX, startWidth;

  handle.addEventListener("mousedown", (e) => {
    startX = e.clientX;
    startWidth = left.offsetWidth;
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  function onMouseMove(e) {
    const dx = e.clientX - startX;
    const newWidth = Math.max(
      300,
      Math.min(window.innerWidth - 300, startWidth + dx),
    );
    left.style.flex = "none";
    left.style.width = newWidth + "px";
  }

  function onMouseUp() {
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }
}

// -- Auto-resize textarea --

function autoResizeInput() {
  const input = document.getElementById("chat-input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
}

document.addEventListener("DOMContentLoaded", () => {
  document
    .getElementById("chat-input")
    .addEventListener("input", autoResizeInput);
});

// -- Markdown rendering --

function renderMarkdown(text) {
  // Convert [[wikilinks]] to styled clickable links before rendering
  const withLinks = text.replace(/\[\[([^\]]+)\]\]/g, (_, name) => {
    const slug = name.toLowerCase().replace(/\s+/g, "-");
    return `[${name}](${slug} "wiki")`;
  });
  return marked.parse(withLinks);
}

// -- Utilities --

const _escapeEl = document.createElement("div");
function escapeHtml(str) {
  _escapeEl.textContent = str;
  return _escapeEl.innerHTML;
}

// -- Theme --

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
  document.getElementById("theme-select").value = theme;
}

function loadTheme() {
  const saved = localStorage.getItem("theme") || "everforest";
  setTheme(saved);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

// -- Model Picker --

function setModel(id) {
  selectedModel = id;
  localStorage.setItem("model", id);
}

async function loadModels() {
  const select = document.getElementById("model-select");
  try {
    const res = await fetch("/api/models");
    const models = await res.json();
    select.innerHTML = "";
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.name;
      select.appendChild(opt);
    }
    // Restore saved selection or default to first
    if (selectedModel && models.some((m) => m.id === selectedModel)) {
      select.value = selectedModel;
    } else if (models.length) {
      selectedModel = models[0].id;
      select.value = selectedModel;
    }
  } catch (e) {
    select.innerHTML = '<option value="">Failed to load models</option>';
  }
}

// -- Copy Chat --

function copyChatText() {
  const messages = document.querySelectorAll("#chat-messages .message");
  const lines = [];
  for (const msg of messages) {
    const role = msg.querySelector(".role")?.textContent?.trim() || "";
    const text =
      msg.querySelector(".assistant-text")?.textContent?.trim() ||
      msg.querySelector(".content")?.textContent?.trim() ||
      "";
    if (text) lines.push(`${role}:\n${text}`);
  }
  const full = lines.join("\n\n");
  navigator.clipboard.writeText(full).then(() => {
    const btn = document.querySelector('button[onclick="copyChatText()"]');
    if (btn) {
      btn.textContent = "Copied!";
      setTimeout(() => {
        btn.textContent = "Copy";
      }, 1500);
    }
  });
}

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "C") {
    e.preventDefault();
    copyChatText();
  }
});

// -- Greeting --

async function greetUser() {
  let pending = [];
  try {
    const res = await fetch("/api/sources/pending");
    pending = await res.json();
  } catch (e) {
    // ignore
  }

  if (pending.length) {
    pendingFiles = pending;
    updateIngestBanner();
  }

  let greetMsg =
    "Greet me with a warm 'Yeehaw! Howdy!' — keep it short, casual, and fun. No technical jargon.";
  if (pending.length) {
    greetMsg += ` Mention that there are ${pending.length} file${pending.length === 1 ? "" : "s"} sitting in the pile waiting to be looked at.`;
  }

  startStreaming();
  const msgEl = createAssistantMessage();

  try {
    const fullText = await streamChat(greetMsg, [], msgEl);
    if (fullText) {
      chatHistory.push({ role: "assistant", content: fullText });
    }
  } catch (err) {
    appendToAssistant(msgEl, "Howdy! Welcome back to the wiki.");
  }

  stopStreaming(msgEl);
}

// -- Lint --

function triggerLint() {
  sendMessage("Run a lint check on the wiki.");
}

function analyzeWikiPage(pageName) {
  sendMessage(
    `Read the wiki page "${pageName}" and its source material. ` +
      `Ask me 3-5 questions to fill in gaps, clarify details, or add context ` +
      `that would make this page more useful. Be specific about what's missing.`,
  );
}

// -- Init --

loadTheme();
loadModels();
setupDragDrop();
setupResize();
refreshWiki();
greetUser();
