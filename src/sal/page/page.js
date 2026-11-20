(() => {
  "use strict";

  const DRAG_THRESHOLD_PX = 4;
  const SYNC_INTERVAL_MS = 100;
  const SWITCH_SEEK_TOLERANCE_S = 0.08;
  const SYNC_RATE_LOW_S = 0.02;
  const SYNC_RATE_HIGH_S = 0.035;
  const SYNC_VIDEO_SEEK_THRESHOLD_S = 0.15;
  const SYNC_AUDIO_SEEK_THRESHOLD_S = 0.072;
  const VIDEO_SEEK_SETTLE_MS = 650;
  const AUDIO_SEEK_SETTLE_MS = 850;

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
  const videoStageEl = document.getElementById("video-stage");
  const playBtnEl = document.getElementById("play");
  const timeEl = document.getElementById("time");
  const levelMatchEl = document.getElementById("level-match");
  const loopStatusEl = document.getElementById("loop-status");
  const commentBoxEl = document.getElementById("comment-box");
  const commentTargetEl = document.getElementById("comment-target");
  const commentKindEl = document.getElementById("comment-kind");
  const commentTextEl = document.getElementById("comment-text");
  const commentSaveEl = document.getElementById("comment-save");
  const commentCancelEl = document.getElementById("comment-cancel");
  const commentStatusEl = document.getElementById("comment-status");

  function isWebKitEngine() {
    const ua = navigator.userAgent || "";
    return (
      ua.includes("AppleWebKit/") &&
      !ua.includes("Chrome") &&
      !ua.includes("Chromium") &&
      !ua.includes("Edg") &&
      !ua.includes("OPR")
    );
  }

  const isServed = window.location.protocol === "http:";
  const needsSilentWebAudioStartup = isWebKitEngine();

  let activeIndex = 1;
  let selection = null; // { index, sha256, start, end }
  let commentTarget = null; // { sha256, start, end }
  let isSaving = false;
  let loopActive = false;
  let rafId = null;
  let syncTimerId = null;
  let audioCtx = null;

  const rows = [];

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function rowDuration(row) {
    return Number.isFinite(row.video.duration) && row.video.duration > 0
      ? row.video.duration
      : row.take.duration;
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
    const rounded = Math.round(gainDb * 10) / 10;
    const normalized = Object.is(rounded, -0) || rounded === 0 ? 0 : rounded;
    return `${normalized.toFixed(1)} dB`;
  }

  function formatTime(seconds) {
    return seconds.toFixed(3);
  }

  function formatClock(seconds) {
    const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
    const totalTenths = Math.round(safe * 10);
    const tenth = totalTenths % 10;
    const totalSecs = Math.floor(totalTenths / 10);
    const secs = totalSecs % 60;
    const mins = Math.floor(totalSecs / 60);
    return `${mins}:${String(secs).padStart(2, "0")}.${tenth}`;
  }

  function activeRow() {
    return rows[activeIndex - 1];
  }

  function updateStageVisibility() {
    if (!videoStageEl) {
      return;
    }
    const current = activeRow();
    videoStageEl.hidden = !(current && current.hasPicture);
  }

  function refreshVideoPicture(row) {
    const hasPic = row.video.videoWidth > 0;
    row.hasPicture = hasPic;
    row.video.classList.toggle("has-picture", hasPic);
    row.video.classList.toggle("no-picture", !hasPic);
    if (row.index === activeIndex) {
      updateStageVisibility();
    }
  }

  function updatePlayButton() {
    if (!playBtnEl) {
      return;
    }
    const current = activeRow();
    const isPlaying = Boolean(current && !current.video.paused && !current.video.ended);
    playBtnEl.setAttribute("data-state", isPlaying ? "playing" : "paused");
    playBtnEl.textContent = isPlaying ? "Pause" : "Play";
  }

  function updateTimeReadout() {
    if (!timeEl) {
      return;
    }
    const current = activeRow();
    if (!current) {
      return;
    }
    const pos = clamp(current.video.currentTime || 0, 0, rowDuration(current));
    const dur = rowDuration(current);
    timeEl.textContent = `${formatClock(pos)} / ${formatClock(dur)}`;
  }

  function ensureAudioGain(row) {
    if (!needsSilentWebAudioStartup) {
      return null;
    }
    if (row.gainNode) {
      return row.gainNode;
    }
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) {
      return null;
    }
    if (!audioCtx) {
      audioCtx = new AudioCtx();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    try {
      const source = audioCtx.createMediaElementSource(row.video);
      const gain = audioCtx.createGain();
      gain.gain.value = row.index === activeIndex && !row.startupUnmuted ? 1 : 0;
      source.connect(gain);
      gain.connect(audioCtx.destination);
      row.gainNode = gain;
      return gain;
    } catch (_err) {
      return null;
    }
  }

  function desiredLinearGain(row) {
    const matched = levelMatchEl ? levelMatchEl.checked : true;
    return matched ? clamp(Math.pow(10, row.take.gain_db / 20), 0, 1) : 1.0;
  }

  function updateVolumes() {
    for (const row of rows) {
      row.video.volume = desiredLinearGain(row);
    }
  }

  function updateMuteAndActive() {
    for (const row of rows) {
      const isActive = row.index === activeIndex;
      row.root.classList.toggle("active", isActive);
      if (isActive) {
        row.startupUnmuted = false;
        row.video.muted = false;
      } else if (!row.startupUnmuted) {
        row.video.muted = true;
      }
      if (row.gainNode) {
        row.gainNode.gain.value = isActive ? 1 : 0;
      }
    }
    updateStageVisibility();
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
    commentBoxEl.scrollIntoView({ block: "nearest" });
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

  function seekAll(timeSeconds) {
    const current = activeRow();
    const wasPlaying = Boolean(current && !current.video.paused && !current.video.ended);
    for (const row of rows) {
      const duration = rowDuration(row);
      const clamped = clamp(timeSeconds, 0, duration);
      row.video.playbackRate = 1;
      row.awaitingSeekCalibration = false;
      row.video.currentTime = clamped;
    }
    if (wasPlaying) {
      for (const row of rows) {
        if (row.video.paused) {
          playVideoSafe(row.video);
        }
      }
    }
    updateAllPlayheads();
    updateTimeReadout();
    updatePlayButton();
  }

  function playRowForSync(row) {
    // WebKit truncates muted audio-only .mp3/.opus <video> elements at t=0 unless
    // started unmuted; route through a zero-gain WebAudio node for silent startup.
    if (
      needsSilentWebAudioStartup &&
      !row.hasPicture &&
      row.index !== activeIndex &&
      row.video.currentTime < 0.01
    ) {
      const gain = ensureAudioGain(row);
      if (gain) {
        gain.gain.value = 0;
        row.startupUnmuted = true;
        row.video.muted = false;
      }
    }
    playVideoSafe(row.video);
  }

  function syncSeekRow(row, masterTime, duration) {
    const now = performance.now();
    const lead = row.hasPicture ? 0 : row.seekLead;
    const target = clamp(masterTime + lead, 0, duration);
    row.lastSeekAt = now;
    row.awaitingSeekCalibration = !row.hasPicture;
    row.video.playbackRate = 1;
    row.video.currentTime = target;
  }

  function syncMutedTakes() {
    const master = activeRow();
    if (!master || master.video.paused || master.video.ended) {
      return;
    }
    if (master.video.playbackRate !== 1) {
      master.video.playbackRate = 1;
    }
    const now = performance.now();
    const masterTime = master.video.currentTime;
    for (const row of rows) {
      if (row === master) {
        continue;
      }
      if (row.startupUnmuted && !row.video.paused && row.video.currentTime > 0.01) {
        row.startupUnmuted = false;
        row.video.muted = true;
      }
      const duration = rowDuration(row);
      if (masterTime >= duration - 0.05) {
        continue;
      }
      const settleMs = row.hasPicture ? VIDEO_SEEK_SETTLE_MS : AUDIO_SEEK_SETTLE_MS;
      if (row.video.seeking || now - row.lastSeekAt < settleMs) {
        continue;
      }
      if (row.awaitingSeekCalibration) {
        row.awaitingSeekCalibration = false;
        if (!row.video.paused) {
          const residual = masterTime - row.video.currentTime;
          const factor = residual > 0 ? 0.95 : 0.5;
          row.seekLead = clamp(row.seekLead + residual * factor, 0, 0.45);
        }
      }
      const diff = masterTime - row.video.currentTime;
      const absDiff = Math.abs(diff);

      if (row.video.paused) {
        if (absDiff > 0.05) {
          syncSeekRow(row, masterTime, duration);
        } else if (row.video.playbackRate !== 1) {
          row.video.playbackRate = 1;
        }
        playRowForSync(row);
        continue;
      }

      if (!row.hasPicture) {
        // WebKit truncates duration on muted .mp3/.opus <video> if playbackRate != 1;
        // sync audio-only takes by seek with measured seek-latency lead.
        if (row.video.playbackRate !== 1) {
          row.video.playbackRate = 1;
        }
        if (absDiff > SYNC_AUDIO_SEEK_THRESHOLD_S) {
          syncSeekRow(row, masterTime, duration);
        }
        continue;
      }

      if (absDiff > SYNC_VIDEO_SEEK_THRESHOLD_S) {
        syncSeekRow(row, masterTime, duration);
      } else if (absDiff > SYNC_RATE_HIGH_S) {
        const step = absDiff > 0.09 ? 0.08 : 0.05;
        const targetRate = diff > 0 ? 1 + step : 1 - step;
        if (row.video.playbackRate !== targetRate) {
          row.video.playbackRate = targetRate;
        }
      } else if (absDiff <= SYNC_RATE_LOW_S && row.video.playbackRate !== 1) {
        row.video.playbackRate = 1;
      }
    }
  }

  function startSyncTimer() {
    if (syncTimerId !== null) {
      return;
    }
    syncTimerId = window.setInterval(() => {
      syncMutedTakes();
      updateTimeReadout();
      updatePlayButton();
    }, SYNC_INTERVAL_MS);
  }

  function stopSyncTimer() {
    if (syncTimerId !== null) {
      window.clearInterval(syncTimerId);
      syncTimerId = null;
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
            playRowForSync(row);
          }
        }
      }
      updateAllPlayheads();
      updateTimeReadout();
      updatePlayButton();
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
    stopSyncTimer();
    const current = activeRow();
    const masterTime = current.video.currentTime;
    for (const row of rows) {
      row.video.pause();
      row.video.playbackRate = 1;
      row.startupUnmuted = false;
      row.awaitingSeekCalibration = false;
      row.video.muted = row !== current;
      if (row.gainNode) {
        row.gainNode.gain.value = row === current ? 1 : 0;
      }
      if (row !== current && Math.abs(row.video.currentTime - masterTime) > 0.02) {
        row.video.currentTime = clamp(masterTime, 0, rowDuration(row));
      }
    }
    updateAllPlayheads();
    updateTimeReadout();
    updatePlayButton();
  }

  function playAll() {
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
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
        if (row !== current && Math.abs(row.video.currentTime - masterTime) > 0.02) {
          row.video.currentTime = clamp(masterTime, 0, rowDuration(row));
        }
      }
    }
    for (const row of rows) {
      row.video.playbackRate = 1;
      if (
        needsSilentWebAudioStartup &&
        !row.hasPicture &&
        row !== current &&
        row.video.currentTime < 0.01
      ) {
        const gain = ensureAudioGain(row);
        if (gain) {
          gain.gain.value = 0;
          row.startupUnmuted = true;
          row.video.muted = false;
        }
      }
    }
    for (const row of rows) {
      if (row !== current) {
        playVideoSafe(row.video);
      }
    }
    playVideoSafe(current.video);
    startRaf();
    startSyncTimer();
    updateTimeReadout();
    updatePlayButton();
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
    const next = rows[nextIndex - 1];
    const masterTime = prev.video.currentTime;
    const wasPlaying = !prev.video.paused && !prev.video.ended;

    // Move the target take while it is still muted when it differs by more than 0.08 s.
    if (Math.abs(next.video.currentTime - masterTime) > SWITCH_SEEK_TOLERANCE_S) {
      next.video.currentTime = clamp(masterTime, 0, rowDuration(next));
    }
    next.video.playbackRate = 1;

    activeIndex = nextIndex;
    updateMuteAndActive();

    if (wasPlaying) {
      if (next.video.paused) {
        playVideoSafe(next.video);
      }
      for (const row of rows) {
        if (row !== next) {
          if (Math.abs(row.video.currentTime - next.video.currentTime) > SYNC_VIDEO_SEEK_THRESHOLD_S) {
            syncSeekRow(row, next.video.currentTime, rowDuration(row));
          }
          if (row.video.paused) {
            playRowForSync(row);
          }
        }
      }
      startRaf();
      startSyncTimer();
    } else {
      for (const row of rows) {
        row.video.playbackRate = 1;
        if (row !== next && Math.abs(row.video.currentTime - next.video.currentTime) > 0.02) {
          row.video.currentTime = clamp(next.video.currentTime, 0, rowDuration(row));
        }
      }
    }
    updateAllPlayheads();
    updateTimeReadout();
    updatePlayButton();
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

    const asideEl = document.createElement("div");
    asideEl.className = "take-aside";

    const videoEl = document.createElement("video");
    videoEl.className = "no-picture";
    videoEl.src = take.media;
    videoEl.preload = "auto";
    videoEl.playsInline = true;
    videoEl.muted = index !== 1;

    const gainEl = document.createElement("span");
    gainEl.className = "take-gain";
    gainEl.textContent = formatGain(take.gain_db);

    asideEl.append(videoEl, gainEl);
    headerEl.append(titleEl, asideEl);

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
    rowEl.append(headerEl, viewEl);
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
      hasPicture: false,
      gainNode: null,
      startupUnmuted: false,
      seekLead: 0,
      lastSeekAt: 0,
      awaitingSeekCalibration: false,
    };
    rows.push(row);

    labelEl.addEventListener("click", () => {
      switchTake(row.index);
    });
    videoEl.addEventListener("click", () => {
      if (row.index === activeIndex) {
        togglePlayPause();
      }
    });

    imgEl.addEventListener("load", () => updatePlayheadForRow(row));
    videoEl.addEventListener("loadedmetadata", () => {
      refreshVideoPicture(row);
      updatePlayheadForRow(row);
      if (row.index === activeIndex) {
        updateTimeReadout();
      }
    });
    videoEl.addEventListener("resize", () => refreshVideoPicture(row));
    videoEl.addEventListener("play", () => {
      if (row.index === activeIndex) {
        updatePlayButton();
      }
    });
    videoEl.addEventListener("pause", () => {
      if (row.index === activeIndex) {
        updatePlayButton();
      }
    });
    videoEl.addEventListener("timeupdate", () => {
      if (row.index === activeIndex && loopActive && selection && selection.end > selection.start) {
        if (videoEl.currentTime >= selection.end) {
          seekAll(selection.start);
          return;
        }
      }
      updatePlayheadForRow(row);
      if (row.index === activeIndex) {
        updateTimeReadout();
      }
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
        startSyncTimer();
      } else {
        stopRaf();
        stopSyncTimer();
        for (const r of rows) {
          r.video.playbackRate = 1;
          if (r !== row && !r.video.paused) {
            r.video.pause();
          }
        }
        updateAllPlayheads();
        updateTimeReadout();
        updatePlayButton();
      }
    });

    if (videoEl.readyState >= 1) {
      refreshVideoPicture(row);
    }

    wireViewPointer(row);
  });

  if (playBtnEl) {
    playBtnEl.addEventListener("click", (event) => {
      if (event.detail > 0) {
        playBtnEl.blur();
      }
      togglePlayPause();
    });
  }
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

    const activeEl = document.activeElement;
    const inCommentField =
      activeEl instanceof HTMLElement &&
      (activeEl === commentTextEl ||
        activeEl === commentKindEl ||
        activeEl.tagName === "TEXTAREA" ||
        activeEl.tagName === "SELECT" ||
        (activeEl.tagName === "INPUT" && activeEl.getAttribute("type") !== "checkbox") ||
        activeEl.isContentEditable);

    if (
      event.key === "Enter" &&
      (event.ctrlKey || event.metaKey) &&
      !event.altKey &&
      activeEl === commentTextEl
    ) {
      event.preventDefault();
      void saveComment();
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey || inCommentField) {
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

    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const current = activeRow();
      const step = event.shiftKey ? 5 : 1;
      const delta = event.key === "ArrowRight" ? step : -step;
      seekAll(clamp(current.video.currentTime + delta, 0, rowDuration(current)));
      return;
    }

    if (event.key === "c" || event.key === "C") {
      event.preventDefault();
      const current = activeRow();
      const t = clamp(current.video.currentTime || 0, 0, rowDuration(current));
      openCommentBox(current, t, t);
      if (commentTextEl) {
        commentTextEl.focus();
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
  updateTimeReadout();
  updatePlayButton();
})();
