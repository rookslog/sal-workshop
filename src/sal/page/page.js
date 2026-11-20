(() => {
  "use strict";

  const DRAG_THRESHOLD_PX = 4;

  const pageDataEl = document.getElementById("page-data");
  if (!pageDataEl) {
    return;
  }
  const pageData = JSON.parse(pageDataEl.textContent || "{}");
  const takes = Array.isArray(pageData.takes) ? pageData.takes : [];
  if (takes.length === 0) {
    return;
  }

  const questionEl = document.getElementById("question");
  if (questionEl) {
    questionEl.textContent = pageData.question || "";
  }

  const takesRoot = document.getElementById("takes");
  const levelMatchEl = document.getElementById("level-match");
  const loopStatusEl = document.getElementById("loop-status");
  const commentBoxEl = document.getElementById("comment-box");
  const commentTargetEl = document.getElementById("comment-target");
  const commentKindEl = document.getElementById("comment-kind");
  const commentTextEl = document.getElementById("comment-text");
  const commentSaveEl = document.getElementById("comment-save");
  const commentCancelEl = document.getElementById("comment-cancel");
  const commentStatusEl = document.getElementById("comment-status");

  const isServed = window.location.protocol === "http:";

  let activeIndex = 1;
  let selection = null; // { index, sha256, start, end }
  let commentTarget = null; // { sha256, start, end }
  let isSaving = false;
  let loopActive = false;
  let rafId = null;

  const rows = [];

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function timeToX(t, ruler) {
    const span = ruler.t1 - ruler.t0;
    if (span <= 0) {
      return ruler.x0;
    }
    return ruler.x0 + (ruler.x1 - ruler.x0) * ((t - ruler.t0) / span);
  }

  function clientXToTime(clientX, row) {
    const rect = row.img.getBoundingClientRect();
    const view = row.take.view;
    const ruler = view.ruler;
    if (rect.width <= 0) {
      return ruler.t0;
    }
    const imgX = ((clientX - rect.left) / rect.width) * view.width;
    const clampedX = clamp(imgX, ruler.x0, ruler.x1);
    const t = ruler.t0 + (ruler.t1 - ruler.t0) * ((clampedX - ruler.x0) / (ruler.x1 - ruler.x0));
    return clamp(t, ruler.t0, ruler.t1);
  }

  function formatGain(gainDb) {
    const normalized = gainDb === 0 ? 0 : gainDb;
    return `${normalized.toFixed(1)} dB`;
  }

  function formatTime(seconds) {
    return seconds.toFixed(3);
  }

  function updateVolumes() {
    const matched = levelMatchEl ? levelMatchEl.checked : true;
    for (const row of rows) {
      if (matched) {
        const linear = Math.pow(10, row.take.gain_db / 20);
        row.video.volume = clamp(linear, 0, 1);
      } else {
        row.video.volume = 1.0;
      }
    }
  }

  function updateMuteAndActive() {
    for (const row of rows) {
      const isActive = row.index === activeIndex;
      row.root.classList.toggle("active", isActive);
      row.video.muted = !isActive;
    }
  }

  function updatePlayheadForRow(row) {
    const view = row.take.view;
    const ruler = view.ruler;
    const t = clamp(row.video.currentTime || 0, ruler.t0, ruler.t1);
    const xPct = (timeToX(t, ruler) / view.width) * 100;
    row.playhead.style.left = `${xPct}%`;
  }

  function updateAllPlayheads() {
    for (const row of rows) {
      updatePlayheadForRow(row);
    }
  }

  function renderSelectionOverlay() {
    for (const row of rows) {
      if (selection && selection.index === row.index && selection.end > selection.start) {
        const view = row.take.view;
        const ruler = view.ruler;
        const xStartPct = (timeToX(selection.start, ruler) / view.width) * 100;
        const xEndPct = (timeToX(selection.end, ruler) / view.width) * 100;
        row.selection.style.left = `${xStartPct}%`;
        row.selection.style.width = `${Math.max(0, xEndPct - xStartPct)}%`;
        row.selection.hidden = false;
      } else {
        row.selection.hidden = true;
      }
    }
  }

  function updateLoopStatus() {
    if (!loopStatusEl) {
      return;
    }
    if (loopActive && selection && selection.end > selection.start) {
      loopStatusEl.textContent = `Loop: ${formatTime(selection.start)}s–${formatTime(selection.end)}s`;
      loopStatusEl.setAttribute("data-active", "true");
    } else {
      loopStatusEl.textContent = "Loop: off";
      loopStatusEl.setAttribute("data-active", "false");
    }
  }

  function setCommentStatus(state, message, saveDisabled) {
    if (commentStatusEl) {
      commentStatusEl.setAttribute("data-state", state);
      commentStatusEl.textContent = message;
    }
    if (commentSaveEl) {
      commentSaveEl.disabled = saveDisabled;
    }
  }

  function setOfflineStatus() {
    setCommentStatus("offline", "Offline: comments cannot be saved.", true);
  }

  function renderCommentMarks(records) {
    if (!Array.isArray(records)) {
      return;
    }
    for (const row of rows) {
      row.marks.textContent = "";
    }
    for (const record of records) {
      if (!record || typeof record !== "object") {
        continue;
      }
      if (pageData.round && record.round !== pageData.round) {
        continue;
      }
      if (!record.id || !record.take || !record.span) {
        continue;
      }
      const row = rows.find((item) => item.take.sha256 === record.take.sha256);
      if (!row) {
        continue;
      }
      const start = Number(record.span.start);
      const end = Number(record.span.end);
      if (!Number.isFinite(start) || !Number.isFinite(end)) {
        continue;
      }
      const view = row.take.view;
      const ruler = view.ruler;
      const xStartPx = timeToX(clamp(start, ruler.t0, ruler.t1), ruler);
      const xEndPx = timeToX(clamp(end, ruler.t0, ruler.t1), ruler);
      const markEl = document.createElement("div");
      markEl.setAttribute("data-id", String(record.id));
      if (end <= start || Math.abs(xEndPx - xStartPx) < 1) {
        markEl.className = "comment-mark comment-mark-moment";
        markEl.style.left = `${(xStartPx / view.width) * 100}%`;
      } else {
        markEl.className = "comment-mark";
        markEl.style.left = `${(xStartPx / view.width) * 100}%`;
        markEl.style.width = `${((xEndPx - xStartPx) / view.width) * 100}%`;
      }
      row.marks.append(markEl);
    }
  }

  async function fetchAndRenderComments() {
    const response = await fetch("comments", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`GET comments failed: ${response.status}`);
    }
    const records = await response.json();
    if (!Array.isArray(records)) {
      throw new Error("Invalid comments payload");
    }
    renderCommentMarks(records);
    return records;
  }

  function openCommentBox(row, start, end) {
    if (!commentBoxEl) {
      return;
    }
    const startStr = formatTime(start);
    const endStr = formatTime(end);
    commentTarget = {
      sha256: row.take.sha256,
      start: Number(startStr),
      end: Number(endStr),
    };
    commentBoxEl.setAttribute("data-start", startStr);
    commentBoxEl.setAttribute("data-end", endStr);
    commentBoxEl.setAttribute("data-sha256", row.take.sha256);
    if (commentTargetEl) {
      if (startStr === endStr) {
        commentTargetEl.textContent = `${row.take.shown} @ ${startStr}s`;
      } else {
        commentTargetEl.textContent = `${row.take.shown} @ ${startStr}s – ${endStr}s`;
      }
    }
    if (isServed) {
      setCommentStatus("ready", "", false);
    } else {
      setOfflineStatus();
    }
    commentBoxEl.hidden = false;
  }

  async function saveComment() {
    if (!isServed || !commentTarget || isSaving) {
      return;
    }
    isSaving = true;
    setCommentStatus("saving", "Saving...", true);
    const payload = {
      sha256: commentTarget.sha256,
      start: commentTarget.start,
      end: commentTarget.end,
      kind: commentKindEl ? commentKindEl.value : "flag",
      text: commentTextEl ? commentTextEl.value : "",
    };
    try {
      const postResponse = await fetch("comments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (postResponse.status !== 201) {
        throw new Error(`POST comments failed: ${postResponse.status}`);
      }
      await fetchAndRenderComments();
      if (commentTextEl) {
        commentTextEl.value = "";
      }
      if (commentKindEl) {
        commentKindEl.value = "flag";
      }
      closeCommentBox();
      setCommentStatus("saved", "Saved.", false);
    } catch (_err) {
      setCommentStatus("error", "Could not save comment.", false);
    } finally {
      isSaving = false;
    }
  }

  function closeCommentBox() {
    if (commentBoxEl && !commentBoxEl.hidden) {
      commentBoxEl.hidden = true;
    }
    const activeEl = document.activeElement;
    if (
      activeEl instanceof HTMLElement &&
      (activeEl === commentTextEl || activeEl === commentKindEl)
    ) {
      activeEl.blur();
    }
  }

  function seekAll(timeSeconds) {
    for (const row of rows) {
      const duration = Number.isFinite(row.video.duration)
        ? row.video.duration
        : row.take.duration;
      const clamped = clamp(timeSeconds, 0, duration);
      row.video.currentTime = clamped;
    }
    updateAllPlayheads();
  }

  function activeRow() {
    return rows[activeIndex - 1];
  }

  function playVideoSafe(video) {
    try {
      const maybePromise = video.play();
      if (maybePromise && typeof maybePromise.catch === "function") {
        maybePromise.catch(() => {});
      }
    } catch (_err) {
      // Ignore synchronous playback errors in headless environments.
    }
  }

  function startRaf() {
    if (rafId !== null) {
      return;
    }
    const tick = () => {
      rafId = null;
      const current = activeRow();
      if (!current) {
        return;
      }
      if (
        loopActive &&
        selection &&
        selection.end > selection.start &&
        !current.video.paused &&
        (current.video.currentTime >= selection.end || current.video.ended)
      ) {
        seekAll(selection.start);
        for (const row of rows) {
          if (row.video.paused) {
            playVideoSafe(row.video);
          }
        }
      }
      updateAllPlayheads();
      if (!current.video.paused && !current.video.ended) {
        rafId = window.requestAnimationFrame(tick);
      }
    };
    rafId = window.requestAnimationFrame(tick);
  }

  function stopRaf() {
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function pauseAll() {
    stopRaf();
    const masterTime = activeRow().video.currentTime;
    for (const row of rows) {
      row.video.pause();
      if (Math.abs(row.video.currentTime - masterTime) > 0.05) {
        row.video.currentTime = masterTime;
      }
    }
    updateAllPlayheads();
  }

  function playAll() {
    const current = activeRow();
    if (loopActive && selection && selection.end > selection.start) {
      if (
        current.video.currentTime < selection.start ||
        current.video.currentTime >= selection.end
      ) {
        seekAll(selection.start);
      }
    } else if (!current.video.ended) {
      const masterTime = current.video.currentTime;
      for (const row of rows) {
        if (row !== current && Math.abs(row.video.currentTime - masterTime) > 0.05) {
          row.video.currentTime = masterTime;
        }
      }
    }
    for (const row of rows) {
      playVideoSafe(row.video);
    }
    startRaf();
  }

  function togglePlayPause() {
    const current = activeRow();
    if (!current.video.paused && !current.video.ended) {
      pauseAll();
    } else {
      playAll();
    }
  }

  function switchTake(nextIndex) {
    if (nextIndex < 1 || nextIndex > rows.length || nextIndex === activeIndex) {
      return;
    }
    const prev = activeRow();
    const masterTime = prev.video.currentTime;
    const wasPlaying = !prev.video.paused && !prev.video.ended;

    activeIndex = nextIndex;
    updateMuteAndActive();

    const next = activeRow();
    if (Math.abs(next.video.currentTime - masterTime) > 0.03) {
      next.video.currentTime = masterTime;
    }
    for (const row of rows) {
      if (row !== next && Math.abs(row.video.currentTime - masterTime) > 0.05) {
        row.video.currentTime = masterTime;
      }
    }
    if (wasPlaying) {
      if (next.video.paused) {
        playVideoSafe(next.video);
      }
      startRaf();
    }
    updateAllPlayheads();
  }

  function toggleLoop() {
    if (!selection || selection.end <= selection.start) {
      return;
    }
    loopActive = !loopActive;
    updateLoopStatus();
    if (loopActive) {
      const current = activeRow();
      if (
        current.video.currentTime < selection.start ||
        current.video.currentTime >= selection.end
      ) {
        seekAll(selection.start);
      }
    }
  }

  function wireViewPointer(row) {
    let activePointerId = null;
    let startClientX = 0;
    let startTime = 0;
    let dragging = false;

    row.view.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      activePointerId = event.pointerId;
      startClientX = event.clientX;
      startTime = clientXToTime(event.clientX, row);
      dragging = false;
      try {
        row.view.setPointerCapture(event.pointerId);
      } catch (_err) {
        // Ignore if pointer capture is unavailable for synthetic events.
      }
    });

    row.view.addEventListener("pointermove", (event) => {
      if (activePointerId !== event.pointerId) {
        return;
      }
      if (!dragging && Math.abs(event.clientX - startClientX) >= DRAG_THRESHOLD_PX) {
        dragging = true;
      }
      if (dragging) {
        const currentTime = clientXToTime(event.clientX, row);
        const start = Math.min(startTime, currentTime);
        const end = Math.max(startTime, currentTime);
        selection = {
          index: row.index,
          sha256: row.take.sha256,
          start,
          end,
        };
        renderSelectionOverlay();
      }
    });

    function finishPointer(event, cancelled) {
      if (activePointerId !== event.pointerId) {
        return;
      }
      activePointerId = null;
      try {
        if (row.view.hasPointerCapture(event.pointerId)) {
          row.view.releasePointerCapture(event.pointerId);
        }
      } catch (_err) {
        // Ignore capture release errors.
      }
      if (cancelled) {
        return;
      }
      const endTime = clientXToTime(event.clientX, row);
      if (dragging) {
        const start = Math.min(startTime, endTime);
        const end = Math.max(startTime, endTime);
        selection = {
          index: row.index,
          sha256: row.take.sha256,
          start,
          end,
        };
        renderSelectionOverlay();
        updateLoopStatus();
        openCommentBox(row, start, end);
      } else {
        selection = {
          index: row.index,
          sha256: row.take.sha256,
          start: endTime,
          end: endTime,
        };
        loopActive = false;
        renderSelectionOverlay();
        updateLoopStatus();
        seekAll(endTime);
        openCommentBox(row, endTime, endTime);
      }
    }

    row.view.addEventListener("pointerup", (event) => finishPointer(event, false));
    row.view.addEventListener("pointercancel", (event) => finishPointer(event, true));
  }

  takes.forEach((take, idx) => {
    const index = idx + 1;
    const rowEl = document.createElement("article");
    rowEl.className = index === 1 ? "take active" : "take";
    rowEl.setAttribute("data-index", String(index));
    rowEl.setAttribute("data-sha256", take.sha256);

    const mediaCol = document.createElement("div");
    mediaCol.className = "take-media";

    const headerEl = document.createElement("div");
    headerEl.className = "take-header";

    const titleEl = document.createElement("div");
    titleEl.className = "take-title";

    const indexEl = document.createElement("span");
    indexEl.className = "take-index";
    indexEl.textContent = String(index);

    const labelEl = document.createElement("span");
    labelEl.className = "take-label";
    labelEl.textContent = take.shown;

    titleEl.append(indexEl, labelEl);

    const gainEl = document.createElement("span");
    gainEl.className = "take-gain";
    gainEl.textContent = formatGain(take.gain_db);

    headerEl.append(titleEl, gainEl);

    const videoEl = document.createElement("video");
    videoEl.src = take.media;
    videoEl.preload = "auto";
    videoEl.playsInline = true;
    videoEl.muted = index !== 1;

    mediaCol.append(headerEl, videoEl);

    const viewEl = document.createElement("div");
    viewEl.className = "view";

    const imgEl = document.createElement("img");
    imgEl.className = "view-image";
    imgEl.src = take.view.png;
    imgEl.alt = `${take.shown} view`;
    imgEl.draggable = false;

    const marksEl = document.createElement("div");
    marksEl.className = "comment-marks";

    const selectionEl = document.createElement("div");
    selectionEl.className = "selection";
    selectionEl.hidden = true;

    const playheadEl = document.createElement("div");
    playheadEl.className = "playhead";

    viewEl.append(imgEl, marksEl, selectionEl, playheadEl);
    rowEl.append(mediaCol, viewEl);
    takesRoot.append(rowEl);

    const row = {
      index,
      take,
      root: rowEl,
      video: videoEl,
      view: viewEl,
      img: imgEl,
      marks: marksEl,
      selection: selectionEl,
      playhead: playheadEl,
    };
    rows.push(row);

    imgEl.addEventListener("load", () => updatePlayheadForRow(row));
    videoEl.addEventListener("loadedmetadata", () => updatePlayheadForRow(row));
    videoEl.addEventListener("timeupdate", () => {
      if (row.index === activeIndex && loopActive && selection && selection.end > selection.start) {
        if (videoEl.currentTime >= selection.end) {
          seekAll(selection.start);
          return;
        }
      }
      updatePlayheadForRow(row);
    });
    videoEl.addEventListener("ended", () => {
      if (row.index !== activeIndex) {
        return;
      }
      if (loopActive && selection && selection.end > selection.start) {
        seekAll(selection.start);
        for (const r of rows) {
          playVideoSafe(r.video);
        }
        startRaf();
      } else {
        stopRaf();
        for (const r of rows) {
          if (r !== row && !r.video.paused) {
            r.video.pause();
          }
        }
        updateAllPlayheads();
      }
    });

    wireViewPointer(row);
  });

  if (levelMatchEl) {
    levelMatchEl.addEventListener("change", updateVolumes);
  }
  if (commentCancelEl) {
    commentCancelEl.addEventListener("click", closeCommentBox);
  }
  if (commentSaveEl) {
    commentSaveEl.addEventListener("click", () => {
      void saveComment();
    });
  }
  window.addEventListener("resize", updateAllPlayheads);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeCommentBox();
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }

    const activeEl = document.activeElement;
    if (
      activeEl instanceof HTMLElement &&
      (activeEl === commentTextEl ||
        activeEl === commentKindEl ||
        activeEl.tagName === "TEXTAREA" ||
        activeEl.tagName === "SELECT" ||
        (activeEl.tagName === "INPUT" && activeEl.getAttribute("type") !== "checkbox") ||
        activeEl.isContentEditable)
    ) {
      return;
    }

    if (event.code === "Space" || event.key === " ") {
      event.preventDefault();
      togglePlayPause();
      return;
    }

    if (event.key >= "1" && event.key <= "9") {
      const idx = Number(event.key);
      if (idx >= 1 && idx <= rows.length) {
        event.preventDefault();
        switchTake(idx);
      }
      return;
    }

    if (event.key === "l" || event.key === "L") {
      if (selection && selection.end > selection.start) {
        event.preventDefault();
        toggleLoop();
      }
    }
  });

  if (isServed) {
    setCommentStatus("ready", "", false);
    fetchAndRenderComments().catch(() => {});
  } else {
    setOfflineStatus();
  }
  updateMuteAndActive();
  updateVolumes();
  updateLoopStatus();
  updateAllPlayheads();
})();
