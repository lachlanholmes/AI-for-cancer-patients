/* Cancer Support — patient-facing client.
   Single-page: form → working → result. SSE-driven streaming. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // --- Agent visual config (matches backend SPECIALIST_CONFIGS) --------------
  const AGENT_VISUALS = {
    physio:     { initials: "P",  label: "Physiotherapist",     color: "#4A7C6F", verb: "looking at movement and exercise" },
    dietician:  { initials: "D",  label: "Dietician",           color: "#8E9F4A", verb: "looking at food and nutrition" },
    slp:        { initials: "S",  label: "Speech & Swallowing", color: "#5A8FA8", verb: "checking for speech/swallowing concerns" },
    mental:     { initials: "E",  label: "Emotional Wellbeing", color: "#7A6BAA", verb: "thinking about emotional support" },
    stories:    { initials: "V",  label: "Stories from Others", color: "#B05E6E", verb: "finding stories from people who've been through this" },
    navigator:  { initials: "N",  label: "Patient Navigator",   color: "#C97B3F", verb: "looking up practical help and resources" },
    translator: { initials: "T",  label: "Translator",          color: "#6B5F52", verb: "waiting to translate the final summary" },
  };

  // Order in which placeholder cards appear on the working view.
  // Stories sits between Emotional Wellbeing and Patient Navigator to match the
  // synthesizer's section order (mental → stories → practical).
  const PLACEHOLDER_ROSTER_IDS = ["physio", "dietician", "slp", "mental", "stories", "navigator", "translator"];

  const ACTIVITY_VERBS = {
    started:        "starting...",
    thinking:       "thinking...",
    tool_call:      "looking things up...",
    tool_result:    "reading sources...",
    self_checking:  "double-checking the answer...",
    drafting:       "writing it up...",
    retrieve_or_abstain: "looking for more evidence...",
    tool_loop_capped: "wrapping up...",
  };

  // --- State ----------------------------------------------------------------
  const state = {
    sid: null,
    source: null,
    targetLanguage: "English",
    roster: [],                  // [{id, display_name, color, conditional}]
    agentStatus: new Map(),      // id -> "idle"|"working"|"done"|"skipped"|"error"|"no_evidence"
    agentDetail: new Map(),      // id -> {status, summary, draft_markdown, labels, error, sourceCount}
    phase: null,                 // null|"synthesizing"|"translating"
    englishMarkdown: "",
    translatedMarkdown: "",
    references: [],              // ledger entries
  };

  // --- View switching -------------------------------------------------------
  const views = {
    form:    $("#view-form"),
    working: $("#view-working"),
    result:  $("#view-result"),
  };

  function showView(name) {
    Object.entries(views).forEach(([k, el]) => {
      el.hidden = k !== name;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // --- Form handling --------------------------------------------------------
  const form = $("#patient-form");
  const caseInput = $("#case");
  const caseError = $("#case-error");

  // Restore from sessionStorage (if user refreshed mid-typing)
  try {
    const saved = JSON.parse(sessionStorage.getItem("cs-form") || "null");
    if (saved) {
      caseInput.value = saved.case || "";
      $("#location").value = saved.location || "";
      $("#preferences").value = saved.preferences || "";
      $("#target_language").value = saved.target_language || "English";
    }
  } catch (e) { /* ignore */ }

  function persistForm() {
    try {
      sessionStorage.setItem("cs-form", JSON.stringify({
        case: caseInput.value,
        location: $("#location").value,
        preferences: $("#preferences").value,
        target_language: $("#target_language").value,
      }));
    } catch (e) { /* ignore */ }
  }

  // Wipe the form inputs and their autosaved copy. Used by both "Start over"
  // buttons (on the form and on the results screen) so no prior patient's text
  // can linger in the textarea on a shared device. "Adjust and ask again"
  // deliberately does NOT call this — it preserves the text for editing.
  function clearForm() {
    caseInput.value = "";
    $("#location").value = "";
    $("#preferences").value = "";
    $("#target_language").value = "English";
    $("#ack").checked = false;
    caseInput.setAttribute("aria-invalid", "false");
    caseError.textContent = "";
    sessionStorage.removeItem("cs-form");
  }

  caseInput.addEventListener("input", () => {
    if (caseInput.getAttribute("aria-invalid") === "true") {
      caseInput.setAttribute("aria-invalid", "false");
      caseError.textContent = "";
    }
    persistForm();
  });
  $("#location").addEventListener("input", persistForm);
  $("#preferences").addEventListener("input", persistForm);
  $("#target_language").addEventListener("input", persistForm);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const caseText = caseInput.value.trim();
    const location = $("#location").value.trim();
    const preferences = $("#preferences").value.trim();
    const targetLanguage = $("#target_language").value.trim() || "English";
    const ack = $("#ack").checked;

    if (caseText.length < 20) {
      caseInput.setAttribute("aria-invalid", "true");
      caseError.textContent = "Please tell us a bit more — at least a sentence or two.";
      caseInput.focus();
      return;
    }
    if (!ack) {
      alert("Please confirm you understand this is not medical advice.");
      return;
    }

    state.targetLanguage = targetLanguage;

    const btn = $("#submit-btn");
    btn.disabled = true;
    btn.textContent = "Starting...";

    try {
      const r = await fetch("/api/board", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case: caseText,
          location: location,
          preferences: preferences,
          target_language: targetLanguage,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${r.status}`);
      }
      const data = await r.json();
      state.sid = data.session_id;
      sessionStorage.removeItem("cs-form");
      // Render placeholder cards instantly so the patient sees the helpers
      // line up before the SSE stream delivers the first board_started event.
      renderPlaceholderCards();
      $("#phase-line").textContent = "Connecting to your helpers...";
      showView("working");
      startStream();
    } catch (err) {
      alert(`Couldn't start your session: ${err.message || err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Get help";
    }
  });

  $("#reset-btn").addEventListener("click", () => {
    clearForm();
    caseInput.focus();
  });

  $("#cancel-btn").addEventListener("click", async () => {
    if (state.sid) {
      try { await fetch(`/api/board/${state.sid}`, { method: "DELETE" }); } catch (e) {}
    }
    if (state.source) { state.source.close(); state.source = null; }
    resetState();
    showView("form");
  });

  // "Adjust and ask again" — keep the patient's text so they can tweak it
  // (add detail, narrow it, or just change the target language) without retyping.
  $("#restart-btn").addEventListener("click", () => {
    resetState();
    showView("form");
    caseInput.focus();
  });

  // "Start over" on the results screen — a full wipe from where the text is shown.
  $("#result-reset-btn").addEventListener("click", () => {
    clearForm();
    resetState();
    showView("form");
    caseInput.focus();
  });

  $("#print-btn").addEventListener("click", () => window.print());

  $("#copy-btn").addEventListener("click", async () => {
    const md = state.translatedMarkdown || state.englishMarkdown || "";
    const refs = state.references.map(r =>
      `[${r.label}] ${r.title || ""} — ${r.journal || ""} ${r.url ? "(" + r.url + ")" : ""}`
    ).join("\n");
    const header = "This contains information about my cancer situation. Please share carefully.\n\n";
    const footer = "\n\n---\nSources:\n" + refs + "\n\nThis is not medical advice. Talk to your oncology team.";
    const text = header + md + footer;
    try {
      await navigator.clipboard.writeText(text);
      const btn = $("#copy-btn");
      const orig = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = orig; }, 1800);
    } catch (e) {
      alert("Could not copy. You can select the text and copy it manually.");
    }
  });

  function resetState() {
    state.sid = null;
    state.source = null;
    state.roster = [];
    state.agentStatus.clear();
    state.agentDetail.clear();
    state.phase = null;
    state.englishMarkdown = "";
    state.translatedMarkdown = "";
    state.references = [];
    $("#agent-stack").innerHTML = "";
    $("#phase-line").textContent = "";
    $("#result-markdown").innerHTML = "";
    $("#references-list").innerHTML = "";
  }

  // --- SSE handling ---------------------------------------------------------
  function startStream() {
    const es = new EventSource(`/api/board/${state.sid}/stream`);
    state.source = es;

    es.addEventListener("message", (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      handleEvent(data);
    });

    es.addEventListener("error", () => {
      // Don't surface every reconnect; only if it stays down.
      $("#phase-line").textContent = "Reconnecting...";
    });
  }

  function handleEvent(ev) {
    const type = ev.type;
    const payload = ev.payload || {};
    switch (type) {
      case "board_started":      onBoardStarted(payload); break;
      case "specialist_event":   onSpecialistEvent(payload); break;
      case "specialist_round_complete": onSpecialistComplete(payload); break;
      case "phase":              onPhase(payload); break;
      case "synthesis_complete": onSynthesisComplete(payload); break;
      case "timing_summary":     break; // logged for diagnostics only
      case "final":              onFinal(payload); break;
      case "error":              onError(payload); break;
      default:                   break;
    }
  }

  function renderPlaceholderCards() {
    const stack = $("#agent-stack");
    stack.innerHTML = "";
    PLACEHOLDER_ROSTER_IDS.forEach((id) => {
      const v = AGENT_VISUALS[id];
      const placeholder = {
        id,
        display_name: v.label,
        color: v.color,
        conditional: id === "slp",
      };
      state.agentStatus.set(id, id === "translator" ? "waiting" : "working");
      state.agentDetail.set(id, {
        status: id === "translator" ? "waiting" : "working",
        sourceCount: 0,
      });
      stack.appendChild(makeAgentCard(placeholder));
    });
  }

  function onBoardStarted(p) {
    state.roster = p.specialists || [];
    const stack = $("#agent-stack");
    const presentIds = new Set(state.roster.map((s) => s.id));

    // If a placeholder card was created for an agent that the backend isn't
    // actually running (e.g., SLP pre-filtered out because the case doesn't
    // involve head/neck or brain), drop it cleanly. Otherwise, just refresh
    // the display_name/color from the authoritative roster.
    PLACEHOLDER_ROSTER_IDS.forEach((id) => {
      const card = $(`.agent-card[data-agent="${id}"]`);
      if (!card) return;
      if (!presentIds.has(id)) {
        card.remove();
        state.agentStatus.delete(id);
        state.agentDetail.delete(id);
      }
    });
    // For agents in the roster that didn't have a placeholder yet, add them.
    state.roster.forEach((s) => {
      if (!$(`.agent-card[data-agent="${s.id}"]`)) {
        state.agentStatus.set(s.id, s.id === "translator" ? "waiting" : "working");
        state.agentDetail.set(s.id, { status: s.id === "translator" ? "waiting" : "working", sourceCount: 0 });
        stack.appendChild(makeAgentCard(s));
      }
    });

    if (p.target_language && p.target_language.toLowerCase() !== "english") {
      $("#phase-line").textContent = `Helpers researching · will translate to ${escapeHtml(p.target_language)} at the end.`;
    } else {
      $("#phase-line").textContent = "Helpers researching...";
    }
  }

  function makeAgentCard(s) {
    const visuals = AGENT_VISUALS[s.id] || { initials: s.id[0].toUpperCase(), label: s.display_name, verb: "..." };
    const card = document.createElement("div");
    card.className = "agent-card is-waiting";
    card.setAttribute("role", "listitem");
    card.setAttribute("data-agent", s.id);
    card.innerHTML = `
      <div class="agent-avatar" style="background:${escapeHtml(s.color || '#4A7C6F')}">
        ${escapeHtml(visuals.initials)}
      </div>
      <div class="agent-body">
        <div class="agent-name">${escapeHtml(s.display_name || visuals.label)}</div>
        <div class="agent-status" aria-live="polite">${escapeHtml(s.id === 'translator' ? 'Waiting for the others to finish' : 'Starting...')}</div>
        <div class="agent-sources" hidden></div>
      </div>
      <div class="agent-icon" aria-hidden="true">○</div>
    `;
    return card;
  }

  function findCard(id) {
    return $(`.agent-card[data-agent="${id}"]`);
  }

  function setStatus(id, text) {
    const card = findCard(id);
    if (!card) return;
    const el = card.querySelector(".agent-status");
    if (el) el.textContent = text;
  }

  function setSources(id, n) {
    const card = findCard(id);
    if (!card) return;
    const el = card.querySelector(".agent-sources");
    if (!el) return;
    if (n > 0) {
      el.hidden = false;
      el.textContent = `${n} source${n === 1 ? "" : "s"} found`;
    } else {
      el.hidden = true;
    }
  }

  function setCardState(id, klass, iconChar) {
    const card = findCard(id);
    if (!card) return;
    card.classList.remove("is-waiting", "is-done", "is-skipped", "is-error");
    if (klass) card.classList.add(klass);
    const ic = card.querySelector(".agent-icon");
    if (ic) ic.textContent = iconChar;
  }

  function onSpecialistEvent(p) {
    const id = p.specialist;
    if (!id) return;
    const card = findCard(id);
    if (!card) return;
    card.classList.remove("is-waiting");

    const type = p.type;
    const detail = state.agentDetail.get(id) || {};

    if (type === "tool_result") {
      detail.sourceCount = (detail.sourceCount || 0) + 1;
      state.agentDetail.set(id, detail);
      setSources(id, detail.sourceCount);
      setStatus(id, "reading sources...");
    } else if (ACTIVITY_VERBS[type]) {
      setStatus(id, ACTIVITY_VERBS[type]);
    }
  }

  function onSpecialistComplete(p) {
    const id = p.specialist;
    const status = p.status;
    state.agentStatus.set(id, status);
    const detail = state.agentDetail.get(id) || {};
    detail.status = status;
    detail.summary = p.recommendation_summary || "";
    detail.draft = p.draft_markdown || "";
    detail.labels = p.evidence_labels || [];
    detail.error = p.error || "";
    state.agentDetail.set(id, detail);

    if (status === "skipped") {
      setCardState(id, "is-skipped", "–");
      setStatus(id, "Not relevant to your situation — sat this out");
      setSources(id, 0);
    } else if (status === "error") {
      setCardState(id, "is-error", "!");
      setStatus(id, p.error ? "Something went wrong" : "Error");
    } else if (status === "no_evidence") {
      setCardState(id, "is-error", "!");
      setStatus(id, "Couldn't find trustworthy sources for this");
    } else {
      // done
      setCardState(id, "is-done", "✓");
      const n = (detail.labels || []).length;
      if (n) setStatus(id, `Done — used ${n} source${n === 1 ? "" : "s"}`);
      else setStatus(id, "Done");
    }
  }

  function onPhase(p) {
    const phase = p.phase;
    state.phase = phase;
    if (phase === "synthesizing") {
      $("#phase-line").textContent = "Putting it all together into one summary...";
    } else if (phase === "translating") {
      const lang = p.target_language || state.targetLanguage;
      if (lang && lang.toLowerCase() !== "english") {
        $("#phase-line").textContent = `Translating to ${escapeHtml(lang)}...`;
        // Update translator card if present
        const card = findCard("translator");
        if (card) {
          card.classList.remove("is-waiting");
          setStatus("translator", `Translating to ${lang}...`);
        }
      } else {
        $("#phase-line").textContent = "Almost done...";
      }
    }
  }

  function onSynthesisComplete(p) {
    state.englishMarkdown = p.english_markdown || "";
  }

  function onFinal(p) {
    state.englishMarkdown = p.english_markdown || state.englishMarkdown;
    state.translatedMarkdown = p.translated_markdown || state.englishMarkdown;
    state.references = p.references || [];

    // Mark translator card as done if it was actually used
    const lang = (p.target_language || "").toLowerCase();
    const card = findCard("translator");
    if (card) {
      if (lang && lang !== "english") {
        setCardState("translator", "is-done", "✓");
        setStatus("translator", `Translated to ${p.target_language}`);
      } else {
        setCardState("translator", "is-skipped", "–");
        setStatus("translator", "Not needed — your summary is in English");
      }
    }

    if (state.source) { state.source.close(); state.source = null; }
    renderResult(p);
    showView("result");
  }

  function onError(p) {
    $("#phase-line").textContent = "";
    const msg = p.message || "Something went wrong.";
    alert(`We had a problem: ${msg}`);
    if (state.source) { state.source.close(); state.source = null; }
    showView("form");
  }

  // --- Result rendering ----------------------------------------------------
  function renderResult(p) {
    const targetLang = p.target_language || state.targetLanguage || "English";
    const refCount = (p.references || []).length;
    const activeCount = Array.from(state.agentStatus.values())
      .filter((s) => s === "done").length;

    $("#result-sub").textContent =
      `${activeCount} helper${activeCount === 1 ? "" : "s"} weighed in · ${refCount} source${refCount === 1 ? "" : "s"} · in ${targetLang}`;

    const md = state.translatedMarkdown || state.englishMarkdown || "*Your summary is empty.*";
    renderMarkdown($("#result-markdown"), md);

    // Build a label → reference map for the inline citation hover tooltip.
    state.refsByLabel = new Map(
      (p.references || []).map((ref) => [String(ref.label), ref])
    );

    const refsList = $("#references-list");
    refsList.innerHTML = "";
    (p.references || []).forEach((ref) => {
      const li = document.createElement("li");
      li.id = `ref-${escapeAttr(String(ref.label || ""))}`;
      const kind = (ref.source_kind || "other").replace(/[^a-z_]/gi, "");
      li.classList.add(`ref-kind-${kind}`);
      const kindBadge = (() => {
        if (kind === "patient_story") return `<span class="ref-badge ref-badge-story">Story</span>`;
        if (kind === "resource_directory") return `<span class="ref-badge ref-badge-resource">Resource</span>`;
        if (kind === "patient_source") return `<span class="ref-badge ref-badge-source">Patient info</span>`;
        return "";
      })();
      const meta = [];
      if (ref.journal) meta.push(escapeHtml(ref.journal));
      if (ref.year) meta.push(escapeHtml(String(ref.year)));
      if (ref.article_type) meta.push(escapeHtml(ref.article_type));
      const urlHtml = ref.url
        ? `<a href="${escapeAttr(ref.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ref.url)}</a>`
        : "";
      const lay = (ref.lay_summary || "").trim();
      const layHtml = lay
        ? `<div class="ref-lay"><span class="ref-lay-label">In plain English:</span> ${escapeHtml(lay)}</div>`
        : "";
      li.innerHTML = `
        <div>
          ${kindBadge}
          <span class="ref-label">[${escapeHtml(ref.label || "")}]</span>
          <span class="ref-title">${escapeHtml(ref.title || "(no title)")}</span>
        </div>
        <div class="ref-meta">${meta.join(" · ")}${meta.length && urlHtml ? " · " : ""}${urlHtml}</div>
        ${layHtml}
      `;
      refsList.appendChild(li);
    });
  }

  // --- Markdown rendering ---------------------------------------------------
  function renderMarkdown(target, md) {
    if (!window.marked || !window.DOMPurify) {
      target.textContent = md;
      return;
    }
    marked.setOptions({ breaks: true, gfm: true });
    let html = marked.parse(md);
    html = transformCitations(html);
    target.innerHTML = DOMPurify.sanitize(html, {
      ADD_ATTR: ["target", "rel"],
    });
  }

  function transformCitations(html) {
    // Replace [N] / [N, M] / [N-M] outside of <a> tags with citation chips.
    // Keep it simple: regex on the rendered HTML, skipping anchor contents.
    return html.replace(/\[(\d{1,3}(?:\s*[-–,;]\s*\d{1,3})*)\]/g, (match, group) => {
      // group like "1" or "1, 2" or "1-3"
      const nums = (group.match(/\d+/g) || []).map(Number);
      let labels = [];
      if (nums.length === 2 && /[-–]/.test(group)) {
        const [lo, hi] = nums;
        if (hi >= lo && hi - lo < 50) {
          labels = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
        } else {
          labels = nums;
        }
      } else {
        labels = nums;
      }
      return labels.map((n) =>
        `<a class="cite" href="#ref-${n}" data-cite-label="${n}" tabindex="0">[${n}]</a>`
      ).join(" ");
    });
  }

  // --- Hover/focus tooltip for inline [N] citations -------------------------
  // Single tooltip element reused across all hovers. Populated from
  // state.refsByLabel (built in renderResult).
  let citeTooltipEl = null;
  function ensureCiteTooltip() {
    if (citeTooltipEl) return citeTooltipEl;
    const el = document.createElement("div");
    el.className = "cite-tooltip";
    el.setAttribute("role", "tooltip");
    el.hidden = true;
    document.body.appendChild(el);
    citeTooltipEl = el;
    return el;
  }

  function kindBadgeFor(kind) {
    if (kind === "patient_story") return `<span class="ref-badge ref-badge-story">Story</span>`;
    if (kind === "resource_directory") return `<span class="ref-badge ref-badge-resource">Resource</span>`;
    if (kind === "patient_source") return `<span class="ref-badge ref-badge-source">Patient info</span>`;
    return "";
  }

  function showCiteTooltip(anchor, ref) {
    const tt = ensureCiteTooltip();
    const meta = [ref.journal, ref.year, ref.article_type]
      .filter(Boolean)
      .map((v) => escapeHtml(String(v)))
      .join(" · ");
    const urlBit = ref.url
      ? `<a href="${escapeAttr(ref.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>`
      : "";
    const lay = (ref.lay_summary || "").trim();
    const layBlock = lay
      ? `<div class="cite-tooltip-lay">${escapeHtml(lay)}</div>`
      : `<div class="cite-tooltip-lay cite-tooltip-lay-loading">Writing a plain-English summary…</div>`;
    tt.innerHTML = `
      <div class="cite-tooltip-head">
        ${kindBadgeFor(ref.source_kind || "")}
        <span class="cite-tooltip-label">[${escapeHtml(String(ref.label || ""))}]</span>
        <span class="cite-tooltip-title">${escapeHtml(ref.title || "(no title)")}</span>
      </div>
      <div class="cite-tooltip-meta">${meta}${meta && urlBit ? " · " : ""}${urlBit}</div>
      <div class="cite-tooltip-laylabel">In plain English:</div>
      ${layBlock}
    `;
    tt.hidden = false;
    positionCiteTooltip(anchor, tt);

    // If we don't have a lay summary yet, fetch it lazily and update the tooltip
    // (and the references panel card) when it arrives.
    if (!lay && ref.label && state.sid) {
      fetchLaySummary(ref.label).then((text) => {
        if (!text) return;
        ref.lay_summary = text;
        // If the tooltip is still showing THIS ref, update it in place
        if (!tt.hidden) {
          const layEl = tt.querySelector(".cite-tooltip-lay");
          if (layEl) {
            layEl.classList.remove("cite-tooltip-lay-loading");
            layEl.textContent = text;
          }
        }
        // Also refresh the matching reference panel card so it shows the summary
        const refCard = document.getElementById(`ref-${ref.label}`);
        if (refCard && !refCard.querySelector(".ref-lay")) {
          const layDiv = document.createElement("div");
          layDiv.className = "ref-lay";
          layDiv.innerHTML =
            `<span class="ref-lay-label">In plain English:</span> ${escapeHtml(text)}`;
          refCard.appendChild(layDiv);
        }
      });
    }
  }

  // In-flight fetches keyed by label so two quick hovers don't duplicate work.
  const _laySummaryInflight = new Map();
  async function fetchLaySummary(label) {
    if (_laySummaryInflight.has(label)) return _laySummaryInflight.get(label);
    const p = (async () => {
      try {
        const r = await fetch(`/api/board/${encodeURIComponent(state.sid)}/lay_summary/${encodeURIComponent(label)}`);
        if (!r.ok) return "";
        const data = await r.json();
        return (data && data.lay_summary) || "";
      } catch (e) {
        return "";
      } finally {
        _laySummaryInflight.delete(label);
      }
    })();
    _laySummaryInflight.set(label, p);
    return p;
  }

  function hideCiteTooltip() {
    if (citeTooltipEl) citeTooltipEl.hidden = true;
  }

  function positionCiteTooltip(anchor, tt) {
    // Make sure layout is computed
    tt.style.left = "0px";
    tt.style.top = "-9999px";
    const aRect = anchor.getBoundingClientRect();
    const ttRect = tt.getBoundingClientRect();
    const margin = 8;
    let top = aRect.bottom + window.scrollY + margin;
    let left = aRect.left + window.scrollX;
    if (left + ttRect.width > window.scrollX + window.innerWidth - 12) {
      left = window.scrollX + window.innerWidth - ttRect.width - 12;
    }
    if (left < window.scrollX + 12) left = window.scrollX + 12;
    // Flip above if no room below
    if (
      aRect.bottom + ttRect.height + margin > window.innerHeight &&
      aRect.top - ttRect.height - margin > 0
    ) {
      top = aRect.top + window.scrollY - ttRect.height - margin;
    }
    tt.style.top = `${top}px`;
    tt.style.left = `${left}px`;
  }

  // Delegated hover + focus handlers. Cite elements are inside #result-markdown.
  document.addEventListener("mouseover", (e) => {
    const cite = e.target.closest && e.target.closest(".cite");
    if (!cite) return;
    const label = cite.dataset.citeLabel;
    const ref = state.refsByLabel && state.refsByLabel.get(label);
    if (!ref) return;
    showCiteTooltip(cite, ref);
  });
  document.addEventListener("mouseout", (e) => {
    const cite = e.target.closest && e.target.closest(".cite");
    if (!cite) return;
    // Only hide if leaving to outside both the cite and the tooltip
    const related = e.relatedTarget;
    if (
      related &&
      (related === citeTooltipEl ||
        (citeTooltipEl && citeTooltipEl.contains(related)))
    ) {
      return;
    }
    hideCiteTooltip();
  });
  document.addEventListener("focusin", (e) => {
    if (!e.target.classList || !e.target.classList.contains("cite")) return;
    const label = e.target.dataset.citeLabel;
    const ref = state.refsByLabel && state.refsByLabel.get(label);
    if (ref) showCiteTooltip(e.target, ref);
  });
  document.addEventListener("focusout", (e) => {
    if (!e.target.classList || !e.target.classList.contains("cite")) return;
    hideCiteTooltip();
  });
  // Dismiss on Escape for accessibility.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideCiteTooltip();
  });

  // --- Tiny escape helpers --------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();
