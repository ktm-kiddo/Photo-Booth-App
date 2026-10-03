/* =============================================================================
   Photo booth front end: runs in Safari on the iPad.

   The guest's journey:
     1. START    the camera, full screen. Touch anywhere to start.
     2. SHOOT    for each of the 3 photos:
                   choose a filter and frame (up to STYLE_TIMEOUT seconds),
                   tap Take photo (or wait) -> countdown -> flash -> photo
     3. REVIEW   the finished strip: Email, Print, Customize, Retake, Finish
                 - Customize > Photos: a different filter/frame per photo
                 - Customize > Strip design: colours/fonts and their own text
                 (no touches for a while -> back to CHOOSE automatically)

   The Pi does all the real work (photos, strip, email). This file shows the
   screens, runs the timers and talks to the Pi's /api/... endpoints.

   Contents
     1. Setup and helpers        7. Shooting
     2. Kiosk protection         8. Review screen
     3. Sound                    9. Customize editor
     4. Messages                10. Email
     5. Tiles (shared)          11. Printing
     6. Start screen, preview   12. Status checks and start-up
   ============================================================================= */

(function () {
  "use strict";

  // ===========================================================================
  // 1. Setup and helpers
  // ===========================================================================

  // Settings from the Pi's .env (see app.py -> index()).
  const CFG = JSON.parse(document.getElementById("booth-config").textContent);
  const PHOTOS = 3;
  const BLANK_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const el = {
    live: $("screen-live"),
    review: $("screen-review"),
    preview: $("preview"),
    overlayPreview: $("overlay-preview"),
    freeze: $("freeze"),
    countdown: $("countdown"),
    countdownNumber: $("countdown-number"),
    countdownRing: document.querySelector(".countdown-ring"),
    developing: $("developing"),
    flash: $("flash"),
    // start screen
    start: $("screen-start"),
    startPreview: $("start-preview"),
    startTitle: $("start-title"),
    startSub: $("start-sub"),
    // choosing a look for each photo
    stylePanel: $("style-panel"),
    photoTitle: $("photo-title"),
    photoSub: $("photo-sub"),
    slots: Array.from(document.querySelectorAll("#progress-slots .slot")),
    filterGrid: $("filter-grid"),
    frameRow: $("frame-row"),
    framePicker: $("frame-picker"),
    takeBtn: $("take-btn"),
    takeTimerFill: $("take-timer-fill"),
    takeSub: $("take-sub"),
    // review
    stripFrame: $("strip-frame"),
    stripImg: $("strip-img"),
    stripHits: Array.from(document.querySelectorAll(".strip-hit")),
    stripBusy: $("strip-busy"),
    reviewActions: $("review-actions"),
    reviewEditor: $("review-editor"),
    btnEmail: $("btn-email"),
    emailLabel: $("email-label"),
    btnPrint: $("btn-print"),
    printLabel: $("print-label"),
    btnEdit: $("btn-edit"),
    btnRetake: $("btn-retake"),
    btnDone: $("btn-done"),
    doneTimer: $("done-timer"),
    doneProgress: $("done-progress"),
    emailList: $("email-list"),
    // customize editor
    editorTabs: Array.from(document.querySelectorAll("#review-editor .tab")),
    panePhotos: $("pane-photos"),
    paneStrip: $("pane-strip"),
    photoTabs: Array.from(document.querySelectorAll(".photo-tab")),
    editFilterGrid: $("edit-filter-grid"),
    editFrameRow: $("edit-frame-row"),
    editFramePicker: $("edit-frame-picker"),
    btnApplyAll: $("btn-apply-all"),
    captionPicker: $("caption-picker"),
    captionForm: $("caption-form"),
    captionInput: $("caption-input"),
    themeGrid: $("theme-grid"),
    btnEditDone: $("btn-edit-done"),
    // email sheet
    emailSheet: $("email-sheet"),
    emailForm: $("email-form"),
    emailInput: $("email-input"),
    emailError: $("email-error"),
    emailCancel: $("email-cancel"),
    emailSend: $("email-send"),
    emailStatus: $("email-status"),
    emailStatusIcon: $("email-status-icon"),
    emailStatusTitle: $("email-status-title"),
    emailStatusText: $("email-status-text"),
    emailAnother: $("email-another"),
    emailClose: $("email-close"),
    // "leave without your photos?"
    finishSheet: $("finish-sheet"),
    finishEmail: $("finish-email"),
    finishPrint: $("finish-print"),
    finishAnyway: $("finish-anyway"),
    // messages
    toast: $("toast"),
    notice: $("notice"),
    noticeTitle: $("notice-title"),
    noticeText: $("notice-text"),
  };

  // Everything that changes while the booth runs.
  const state = {
    screen: "start",       // "start" | "shoot" | "review"
    phase: null,           // while shooting: "choose" (picking a look) or "countdown"
    filter: "none",
    overlay: "none",
    session: null,         // the current session as last reported by the Pi
    busy: false,           // true while the shooting sequence runs
    lastActivity: Date.now(),
    reviewDeadline: 0,
    emailDeadline: 0,
    editPhoto: 1,          // which photo the customize editor is working on
    reloadPending: false,  // the Pi restarted: reload once the guests are done
    looksPending: false,   // frames/designs changed on the admin page: refresh when free
  };

  /**
   * Call the Pi's API. Pass `body` to make a POST request.
   * Throws an Error with a guest-friendly message if anything goes wrong.
   */
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    const options = { method: "GET", cache: "no-store", signal: controller.signal, headers: {} };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch (err) {
      throw new Error("Can't reach the photo booth. Please try again.");
    } finally {
      clearTimeout(timer);
    }

    let data = {};
    try { data = await response.json(); } catch (err) { /* not JSON */ }
    if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    return data;
  }

  const sessionUrl = (path) => "/api/sessions/" + state.session.id + (path || "");

  /** Restart a CSS animation by removing and re-adding its class. */
  function replayAnimation(element, className) {
    element.classList.remove(className);
    void element.getBoundingClientRect();   // forces the browser to notice
    element.classList.add(className);
  }

  /** Load an image in the background; resolves with its URL when ready (or after 4 s). */
  function preload(src) {
    return new Promise((resolve) => {
      const img = new Image();
      const done = () => resolve(src);
      img.onload = img.onerror = done;
      setTimeout(done, 4000);
      img.src = src;
    });
  }

  // ===========================================================================
  // 2. Kiosk protection: no pinch-zoom, double-tap zoom or long-press menus.
  //    (iPad Safari ignores "user-scalable=no", so this needs JavaScript.)
  // ===========================================================================
  ["gesturestart", "gesturechange", "gestureend"].forEach((type) => {
    document.addEventListener(type, (e) => e.preventDefault(), { passive: false });
  });
  // No scrolling or dragging anywhere (two fingers = pinch, also blocked),
  // except inside the few lists marked "scrollable".
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1 || !e.target.closest(".scrollable")) e.preventDefault();
  }, { passive: false });
  document.addEventListener("dblclick", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("contextmenu", (e) => e.preventDefault());

  // Any touch or typing counts as "someone is using the booth".
  function noteActivity() {
    state.lastActivity = Date.now();
    if (state.screen === "review") state.reviewDeadline = Date.now() + CFG.reviewTimeout * 1000;
    if (!el.emailSheet.hidden) state.emailDeadline = Date.now() + emailSheetTimeout();
  }
  ["pointerdown", "keydown", "input"].forEach((type) => {
    document.addEventListener(type, noteActivity, true);
  });

  // ===========================================================================
  // 3. Sound: beeps and a shutter click, made on the fly with Web Audio.
  //    iOS only allows sound after the first touch, so unlock() runs then.
  // ===========================================================================
  const Sound = (function () {
    let ctx = null;

    function unlock() {
      if (!CFG.sound) return;
      try {
        ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
        if (ctx.state === "suspended") ctx.resume();
      } catch (err) {
        ctx = null;     // no sound available: carry on silently
      }
    }

    function tone(frequency, seconds, volume, delay) {
      if (!ctx) return;
      const start = ctx.currentTime + (delay || 0);
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(volume, start + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + seconds);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + seconds + 0.05);
    }

    function shutter() {
      if (!ctx) return;
      // A burst of filtered noise that fades out quickly sounds like a shutter.
      const length = Math.floor(ctx.sampleRate * 0.14);
      const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < length; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 4);
      }
      const source = ctx.createBufferSource();
      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = 2200;
      source.buffer = buffer;
      source.connect(filter).connect(ctx.destination);
      source.start();
    }

    return {
      unlock: unlock,
      tick: (n) => tone(n === 1 ? 1175 : 880, 0.14, 0.3),
      shutter: shutter,
      tada: () => { tone(784, 0.18, 0.25); tone(1047, 0.18, 0.25, 0.12); tone(1319, 0.35, 0.25, 0.24); },
      ding: () => { tone(1319, 0.12, 0.2); tone(1760, 0.3, 0.2, 0.1); },
    };
  })();

  document.addEventListener("pointerdown", Sound.unlock, true);

  // ===========================================================================
  // 4. Messages: short toasts, and the full-screen notice for real problems.
  // ===========================================================================
  let toastTimer = null;

  function showToast(message, seconds) {
    el.toast.textContent = message;
    el.toast.hidden = true;                    // hide and re-show so the
    void el.toast.getBoundingClientRect();     // slide-in animation replays
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, (seconds || 4) * 1000);
  }

  function showNotice(title, text) {
    el.noticeTitle.textContent = title;
    el.noticeText.textContent = text;
    el.notice.hidden = false;
  }

  function hideNotice() {
    el.notice.hidden = true;
  }

  // ===========================================================================
  // 5. Tiles: the square picture buttons used for filters and frames.
  // ===========================================================================

  /** A button with stacked images and a label underneath. */
  function makeTile(id, label, layers) {
    const button = document.createElement("button");
    button.className = "tile";
    button.dataset.id = id;
    const box = document.createElement("div");
    box.className = "tile-image";
    layers.forEach((layer) => box.appendChild(layer));
    const text = document.createElement("span");
    text.className = "tile-label";
    text.textContent = label;
    button.append(box, text);
    return button;
  }

  function imageLayer(className) {
    const img = document.createElement("img");
    img.alt = "";
    if (className) img.className = className;
    return img;
  }

  const overlayUrl = (id) => "/overlays/" + encodeURIComponent(id) + ".png";

  /**
   * Fill a filter grid and a frame row with buttons. Each picture is marked
   * with the filter it should show (data-look); "current" means "whatever
   * filter is chosen now", used under the frames.
   */
  /** Listen for taps on a filter grid and frame grid (once). */
  function setupPicker(filterGrid, frameRow, onFilter, onFrame) {
    filterGrid.addEventListener("click", (e) => {
      const tile = e.target.closest(".tile");
      if (tile) onFilter(tile.dataset.id);
    });
    frameRow.addEventListener("click", (e) => {
      const tile = e.target.closest(".tile");
      if (tile) onFrame(tile.dataset.id);
    });
  }

  /** (Re)create the filter and frame buttons from CFG. */
  function fillPicker(filterGrid, frameRow, framePicker) {
    filterGrid.innerHTML = "";
    frameRow.innerHTML = "";
    CFG.filters.forEach((f) => {
      const tile = makeTile(f.id, f.name, [imageLayer()]);
      tile.querySelector("img").dataset.look = f.id;
      filterGrid.appendChild(tile);
    });

    // The admin page sets the order, including where "No frame" goes (or hides it).
    CFG.overlays.forEach((o) => {
      const base = imageLayer();
      base.dataset.look = "current";
      const layers = [base];
      if (!o.none) {
        const frame = imageLayer("tile-frame");
        frame.src = overlayUrl(o.id);
        layers.push(frame);
      }
      frameRow.appendChild(makeTile(o.id, o.name, layers));
    });
    // One frame or none? Then there's nothing to choose.
    framePicker.hidden = CFG.overlays.length < 2;
  }

  /** The frame guests start with: the first one on the admin page's list. */
  const firstFrame = () => (CFG.overlays.length ? CFG.overlays[0].id : "none");

  /** Point every picture in a picker at the right image, swapping only once loaded. */
  function refreshPictures(container, pictureFor, currentFilter) {
    container.querySelectorAll("img[data-look]").forEach((img) => {
      const filterId = img.dataset.look === "current" ? currentFilter : img.dataset.look;
      const src = pictureFor(filterId);
      if (img.dataset.src === src) return;
      img.dataset.src = src;
      preload(src).then(() => { if (img.dataset.src === src) img.src = src; });
    });
  }

  function markSelected(container, id) {
    container.querySelectorAll(".tile").forEach((tile) => {
      tile.classList.toggle("is-selected", tile.dataset.id === id);
    });
  }

  /** Build every filter, frame and design button from CFG. */
  function fillLooks() {
    fillPicker(el.filterGrid, el.frameRow, el.framePicker);
    fillPicker(el.editFilterGrid, el.editFrameRow, el.editFramePicker);
    fillThemes();
  }

  /** Fetch the latest frames and designs (changed on the admin page) and rebuild the buttons. */
  async function refreshLooks() {
    try {
      const looks = await api("/api/looks");
      CFG.filters = looks.filters;
      CFG.overlays = looks.overlays;
      CFG.themes = looks.themes;
      state.looksPending = false;
      fillLooks();
    } catch (err) { /* try again with the next status check */ }
  }

  // ===========================================================================
  // 6. Start screen and the live preview
  // ===========================================================================

  // Only one camera stream runs at a time: the camera's whole picture on the
  // start screen, or the photo-shaped one (with the chosen filter) while shooting.
  const activeImage = () =>
    state.screen === "start" ? el.startPreview : state.screen === "shoot" ? el.preview : null;

  function stopStream() {
    el.preview.src = BLANK_IMAGE;          // closes the stream connections
    el.startPreview.src = BLANK_IMAGE;
  }

  function startStream() {
    stopStream();
    const img = activeImage();
    if (img) img.src = "/stream.mjpg?t=" + Date.now() + (img === el.startPreview ? "&shape=full" : "");
  }

  // If the stream breaks (e.g. the Pi restarted), try again shortly.
  [el.preview, el.startPreview].forEach((img) => img.addEventListener("error", () => {
    if (img === activeImage()) setTimeout(startStream, 2000);
  }));

  // Safari pauses the stream when the iPad sleeps; restart it on wake.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && activeImage()) startStream();
  });

  function showStart() {
    state.screen = "start";
    state.session = null;
    el.review.hidden = true;
    el.live.hidden = true;
    el.start.hidden = false;
    api("/api/preview", { filter: "none" }).catch(() => {});
    startStream();
    state.lastActivity = Date.now();
    if (state.reloadPending) window.location.reload();
    else if (state.looksPending) refreshLooks();
  }

  // Touch anywhere on the start screen (including the button) to begin.
  el.start.addEventListener("click", () => runSession(false));

  // ---------------------------------------------------------------------------
  // Choosing a look for each photo
  // ---------------------------------------------------------------------------
  let thumbStamp = Date.now();
  const liveThumb = (filterId) => "/api/thumbs/" + encodeURIComponent(filterId) + ".jpg?t=" + thumbStamp;

  function refreshLiveThumbs() {
    refreshPictures(el.stylePanel, liveThumb, state.filter);
  }

  function selectFilter(id) {
    state.filter = id;
    markSelected(el.filterGrid, id);
    refreshLiveThumbs();
    // Tell the Pi, so the live preview shows the filter straight away.
    api("/api/preview", { filter: id }).catch(() => { /* status check will report */ });
  }

  function selectOverlay(id) {
    state.overlay = id;
    markSelected(el.frameRow, id);
    if (id === "none") {
      el.overlayPreview.hidden = true;
    } else {
      el.overlayPreview.src = overlayUrl(id);
      el.overlayPreview.hidden = false;
    }
  }

  // The little live pictures on the filter buttons update every few seconds.
  setInterval(() => {
    if (state.phase === "choose" && !document.hidden) {
      thumbStamp = Date.now();
      refreshLiveThumbs();
    }
  }, 2500);

  // ===========================================================================
  // 7. Shooting: choose a look (STYLE_TIMEOUT seconds), then three photos in
  //    a row: countdown, flash, next countdown, flash...
  // ===========================================================================
  const RING = 295.3;     // circumference of the Go button's timer ring (r=47)

  async function countdown(seconds) {
    el.countdown.hidden = false;
    for (let n = seconds; n >= 1; n--) {
      el.countdownNumber.textContent = n;
      replayAnimation(el.countdownNumber, "tick");
      replayAnimation(el.countdownRing, "tick");
      Sound.tick(n);
      await sleep(1000);
    }
    el.countdown.hidden = true;
  }

  function fireFlash() {
    replayAnimation(el.flash, "is-firing");
    Sound.shutter();
  }

  function resetSlots() {
    el.slots.forEach((slot) => {
      slot.style.backgroundImage = "";
      slot.classList.remove("is-filled", "is-next");
    });
  }

  function markNextSlot(index) {
    el.slots.forEach((slot, i) => slot.classList.toggle("is-next", i === index - 1));
  }

  function fillSlot(index, url) {
    const slot = el.slots[index - 1];
    slot.classList.remove("is-next");
    slot.style.backgroundImage = 'url("' + url + '")';
    replayAnimation(slot, "is-filled");
  }

  function showLive() {
    state.screen = "shoot";
    el.start.hidden = true;
    el.review.hidden = true;
    el.live.hidden = false;
    el.freeze.hidden = true;
    el.developing.hidden = true;
    el.countdown.hidden = true;
    resetSlots();
    selectFilter(state.filter);          // the live preview shows the current look
    selectOverlay(state.overlay);
    startStream();
  }

  /**
   * Guests choose a filter and frame. Resolves when they tap Go, or when
   * the time runs out (the ring around Go empties).
   */
  function chooseLook() {
    state.phase = "choose";
    el.photoTitle.textContent = "Choose your look";
    el.photoSub.textContent = "Filter and frame";
    el.stylePanel.classList.remove("is-shooting");
    el.takeBtn.disabled = false;
    refreshLiveThumbs();

    const total = CFG.styleTimeout * 1000;
    const deadline = Date.now() + total;
    return new Promise((resolve) => {
      let timer = null;
      const done = () => {
        clearInterval(timer);
        el.takeBtn.removeEventListener("click", done);
        resolve();
      };
      const tick = () => {
        const left = Math.max(0, deadline - Date.now());
        const seconds = Math.ceil(left / 1000);
        const ending = seconds <= 3;
        el.takeTimerFill.style.strokeDashoffset = String(RING * (1 - left / total));
        el.takeTimerFill.classList.toggle("is-ending", ending);
        el.takeSub.classList.toggle("is-ending", ending);
        el.takeSub.textContent = "Starting in " + seconds + "s";
        if (left <= 0) done();
      };
      el.takeBtn.addEventListener("click", done);
      timer = setInterval(tick, 250);
      tick();
    });
  }

  /** The whole shoot: choose a look, three photos, then the strip. */
  async function runSession(keepLook) {
    if (state.busy) return;
    state.busy = true;
    if (!keepLook) {
      state.filter = "none";
      state.overlay = firstFrame();
    }

    try {
      state.session = await api("/api/sessions", { filter: state.filter, overlay: state.overlay });
      showLive();
      await chooseLook();

      state.phase = "countdown";
      el.stylePanel.classList.add("is-shooting");
      el.takeBtn.disabled = true;
      let capture = Promise.resolve();
      for (let i = 1; i <= PHOTOS; i++) {
        el.photoTitle.textContent = "Photo " + i + " of " + PHOTOS;
        el.photoSub.textContent = "Look at the camera";
        markNextSlot(i);
        await countdown(i === 1 ? CFG.countdown : CFG.betweenShots);
        await capture;                      // the previous photo has been saved
        fireFlash();
        // Take the photo, and start the next countdown straight away, so the
        // photos really are BETWEEN_SHOTS seconds apart. Each photo appears in
        // the little strip at the top as soon as it's ready.
        const index = i;
        capture = api(sessionUrl("/capture"), { filter: state.filter, overlay: state.overlay })
          .then((shot) => fillSlot(index, shot.thumb_url));
      }
      await capture;

      state.phase = null;
      el.photoTitle.textContent = "That's a wrap";
      el.photoSub.textContent = "Making your strip";
      el.developing.hidden = false;
      // Show "Developing" for at least 1.5 s; it's part of the fun.
      const results = await Promise.all([api(sessionUrl("/finish"), {}), sleep(1500)]);
      await preload(results[0].strip_url);
      showReview(results[0]);
    } catch (err) {
      showToast(err.message || "Something went wrong. Please try again.");
      if (state.session) api(sessionUrl("/discard"), {}).catch(() => {});
      showStart();
    } finally {
      state.busy = false;
      state.phase = null;
      el.countdown.hidden = true;
      el.developing.hidden = true;
      el.freeze.hidden = true;
    }
  }

  // ===========================================================================
  // 8. Review screen: strip, buttons, idle timer.
  // ===========================================================================
  let pollTimer = null;
  let seen = {};        // what we've already told the guest about (so messages show once)

  function showReview(session) {
    state.screen = "review";
    state.session = session;
    state.editPhoto = 1;
    seen = { print: null, emails: {} };
    stopStream();
    el.live.hidden = true;
    el.review.hidden = false;
    closeEditor();

    // The strip slides out of the slot, like a real photo booth.
    el.stripImg.src = session.strip_url;
    replayAnimation(el.stripFrame, "is-printing");
    Sound.tada();

    el.btnEmail.hidden = !session.features.email;
    el.btnPrint.hidden = !session.features.print;
    el.emailList.innerHTML = "";
    emailListKey = "";

    renderSession(session);
    state.reviewDeadline = Date.now() + CFG.reviewTimeout * 1000;
    clearInterval(pollTimer);
    pollTimer = setInterval(pollSession, 1000);
  }

  async function pollSession() {
    if (state.screen !== "review" || !state.session || editsRunning) return;
    try {
      renderSession(await api(sessionUrl()));
    } catch (err) { /* temporary hiccup: try again next time */ }
  }

  /** Update the review screen to match what the Pi reports. */
  function renderSession(session) {
    const oldStrip = state.session ? state.session.strip_url : null;
    state.session = session;
    if (oldStrip && session.strip_url !== oldStrip) {
      preload(session.strip_url).then((src) => {
        if (state.session && state.session.strip_url === src) el.stripImg.src = src;
      });
    }
    renderPrint(session.print);
    renderEmails(session.emails);
    if (!el.reviewEditor.hidden) renderEditor();
  }

  function tickReviewTimer() {
    if (state.screen !== "review") return;
    if (!el.emailSheet.hidden) {
      // The email sheet has its own timer; keep the review timer topped up.
      state.reviewDeadline = Date.now() + CFG.reviewTimeout * 1000;
      if (Date.now() > state.emailDeadline) closeEmail();
      return;
    }
    const left = Math.max(0, state.reviewDeadline - Date.now());
    const seconds = Math.ceil(left / 1000);
    el.doneTimer.textContent = seconds <= 15 ? seconds + "s" : "";
    el.doneProgress.style.transform = "scaleX(" + left / (CFG.reviewTimeout * 1000) + ")";
    if (left <= 0) finishReview();
  }

  setInterval(tickReviewTimer, 250);

  /** The guest tapped Finish: check they haven't forgotten their photos. */
  function requestFinish() {
    const session = state.session;
    const canTake = session.features.email || session.features.print;
    const tookIt = session.emails.some((e) => e.state !== "failed")
      || ["printing", "done"].indexOf(session.print.state) !== -1;
    if (canTake && !tookIt) {
      el.finishEmail.hidden = !session.features.email;
      el.finishPrint.hidden = !session.features.print;
      el.finishSheet.hidden = false;
      return;
    }
    finishReview();
  }

  function finishReview() {
    clearInterval(pollTimer);
    el.finishSheet.hidden = true;
    closeEmail();
    showToast("Thank you", 3);
    showStart();
  }

  async function retake() {
    if (state.busy) return;
    clearInterval(pollTimer);
    const old = state.session;
    // Start again from the look of photo 1.
    if (old && old.styles && old.styles[0]) {
      state.filter = old.styles[0].filter;
      state.overlay = old.styles[0].overlay;
    }
    if (old) api("/api/sessions/" + old.id + "/discard", {}).catch(() => {});
    runSession(true);
  }

  el.btnDone.addEventListener("click", requestFinish);
  el.finishAnyway.addEventListener("click", finishReview);
  el.finishEmail.addEventListener("click", () => { el.finishSheet.hidden = true; openEmail(); });
  el.finishPrint.addEventListener("click", () => { el.finishSheet.hidden = true; el.btnPrint.click(); });
  el.btnRetake.addEventListener("click", retake);

  // ===========================================================================
  // 9. Customize editor: each photo's filter/frame, and the strip's design.
  // ===========================================================================
  const lookThumb = (filterId) =>
    sessionUrl("/photos/" + state.editPhoto + "/looks/" + encodeURIComponent(filterId) + ".jpg");

  /** (Re)create the strip-design buttons: a square in each design's font and colours. */
  function fillThemes() {
    el.themeGrid.innerHTML = "";
    CFG.themes.forEach((t) => {
      const button = document.createElement("button");
      button.className = "theme-tile";
      button.dataset.id = t.id;
      const swatch = document.createElement("div");
      swatch.className = "theme-swatch";
      swatch.style.background = t.background;
      if (t.art) {
        // Artwork: show the bottom of the strip, where the title goes.
        swatch.style.backgroundImage = "url(/strip-art/" + t.art + ".png)";
        swatch.style.backgroundSize = "100% auto";
        swatch.style.backgroundPosition = "center bottom";
      }
      swatch.style.color = t.text;
      swatch.style.fontFamily = '"' + t.font + '", serif';
      swatch.style.fontWeight = t.weight || 400;
      swatch.textContent = t.upper ? "AA" : "Aa";
      const label = document.createElement("span");
      label.className = "tile-label";
      label.textContent = t.name;
      button.append(swatch, label);
      el.themeGrid.appendChild(button);
    });
  }

  function buildEditor() {
    setupPicker(el.editFilterGrid, el.editFrameRow,
      (filterId) => restyle({ filter: filterId }),
      (overlayId) => restyle({ overlay: overlayId }));
    el.themeGrid.addEventListener("click", (e) => {
      const tile = e.target.closest(".theme-tile");
      if (tile) redesign({ theme: tile.dataset.id });
    });

    // Their own text at the bottom of the strip.
    el.captionPicker.hidden = !CFG.customCaption;
    el.captionInput.maxLength = CFG.captionMax;
    el.captionInput.placeholder = CFG.title;
    el.captionForm.addEventListener("submit", (e) => {
      e.preventDefault();
      el.captionInput.blur();          // the blur handler below applies it
    });
    el.captionInput.addEventListener("blur", () => {
      const caption = el.captionInput.value.trim();
      if (state.session && caption !== state.session.caption) redesign({ caption: caption });
    });

    el.editorTabs.forEach((tab) => tab.addEventListener("click", () => showPane(tab.dataset.pane)));
    el.photoTabs.forEach((tab) => tab.addEventListener("click", () => {
      state.editPhoto = Number(tab.dataset.photo);
      renderEditor();
    }));
    el.stripHits.forEach((hit) => hit.addEventListener("click", () => {
      openEditor("photos", Number(hit.dataset.photo));
    }));
    el.btnEdit.addEventListener("click", () => openEditor("photos", state.editPhoto));
    el.btnApplyAll.addEventListener("click", () => {
      const style = state.session.styles[state.editPhoto - 1];
      restyle({ filter: style.filter, overlay: style.overlay, all: true });
      showToast("Applied to all 3 photos", 2);
    });
    el.btnEditDone.addEventListener("click", closeEditor);
  }

  function openEditor(pane, photo) {
    state.editPhoto = photo;
    el.reviewActions.hidden = true;
    el.reviewEditor.hidden = false;
    el.captionInput.value = state.session.caption || "";
    showPane(pane);
  }

  function closeEditor() {
    el.captionInput.blur();
    el.reviewEditor.hidden = true;
    el.reviewActions.hidden = false;
    el.stripHits.forEach((hit) => hit.classList.remove("is-selected"));
  }

  function showPane(name) {
    el.editorTabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.pane === name));
    el.panePhotos.hidden = name !== "photos";
    el.paneStrip.hidden = name !== "strip";
    renderEditor();
  }

  /** Show the current choices in the editor and highlight the photo being edited. */
  function renderEditor() {
    const session = state.session;
    if (!session || !session.styles || !session.styles.length) return;
    const editingPhotos = !el.panePhotos.hidden;
    const style = session.styles[state.editPhoto - 1];

    el.stripHits.forEach((hit) => {
      hit.classList.toggle("is-selected", editingPhotos && Number(hit.dataset.photo) === state.editPhoto);
    });
    el.photoTabs.forEach((tab) => {
      const n = Number(tab.dataset.photo);
      tab.classList.toggle("is-selected", n === state.editPhoto);
      const src = sessionUrl("/photos/" + n + ".jpg?v=" + session.version);
      const img = tab.querySelector("img");
      if (img.dataset.src !== src) {
        img.dataset.src = src;
        preload(src).then(() => { if (img.dataset.src === src) img.src = src; });
      }
    });
    markSelected(el.editFilterGrid, style.filter);
    markSelected(el.editFrameRow, style.overlay);
    refreshPictures(el.panePhotos, lookThumb, style.filter);

    el.themeGrid.querySelectorAll(".theme-tile").forEach((tile) => {
      tile.classList.toggle("is-selected", tile.dataset.id === session.theme);
    });
  }

  // Changes run one after another, so quick taps can't arrive out of order.
  // A spinner sits on the strip while the Pi rebuilds it.
  let editQueue = Promise.resolve();
  let editsRunning = 0;

  function queueEdit(path, body) {
    editsRunning++;
    el.stripBusy.hidden = false;
    editQueue = editQueue.then(async () => {
      try {
        const session = await api(sessionUrl(path), body);
        await preload(session.strip_url);
        renderSession(session);
      } catch (err) {
        showToast(err.message);
      } finally {
        editsRunning--;
        if (!editsRunning) el.stripBusy.hidden = true;
      }
    });
  }

  function restyle(change) {
    const style = state.session.styles[state.editPhoto - 1];
    const body = Object.assign({ filter: style.filter, overlay: style.overlay }, change);
    // Show the choice straight away; the Pi confirms a moment later.
    if (!change.all) {
      state.session.styles[state.editPhoto - 1] = { filter: body.filter, overlay: body.overlay };
      renderEditor();
    }
    queueEdit("/photos/" + state.editPhoto + "/style", body);
  }

  function redesign(change) {
    const body = Object.assign({ theme: state.session.theme, caption: state.session.caption }, change);
    state.session.theme = body.theme;
    state.session.caption = body.caption;
    renderEditor();
    queueEdit("/design", body);
  }

  // ===========================================================================
  // 10. Email: guests type an address and they're done. The Pi sends it in
  //     the background (waiting for the internet if needed), so they can add
  //     more addresses or leave straight away.
  // ===========================================================================
  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/;
  const CONFIRM_SECONDS = 8;      // the "You're all set" message closes itself

  // The keyboard gets the full timeout; the confirmation only a few seconds.
  const emailSheetTimeout = () => (el.emailStatus.hidden ? CFG.emailTimeout : CONFIRM_SECONDS) * 1000;

  function openEmail() {
    showEmailForm("");
    el.emailSheet.hidden = false;
    state.emailDeadline = Date.now() + emailSheetTimeout();
  }

  function showEmailForm(prefill) {
    el.emailStatus.hidden = true;
    el.emailForm.hidden = false;
    el.emailInput.value = prefill;
    el.emailError.textContent = "";
    el.emailInput.focus();         // brings up the iPad keyboard
    state.emailDeadline = Date.now() + emailSheetTimeout();
  }

  function closeEmail() {
    el.emailInput.blur();
    el.emailSheet.hidden = true;
    state.reviewDeadline = Date.now() + CFG.reviewTimeout * 1000;
  }

  function showEmailConfirmation(address) {
    el.emailInput.blur();
    el.emailForm.hidden = true;
    el.emailStatus.hidden = false;
    el.emailStatusIcon.className = "status-icon is-sent";
    el.emailStatusIcon.textContent = "✓";
    el.emailStatusTitle.textContent = "You're all set";
    el.emailStatusText.textContent = "Your photos are on their way to " + address +
      ". They usually arrive within a few minutes; if not, check the spam folder.";
    el.emailAnother.textContent = "Add another email";
    el.emailClose.textContent = "Done";
    state.emailDeadline = Date.now() + emailSheetTimeout();
  }

  el.btnEmail.addEventListener("click", openEmail);
  el.emailCancel.addEventListener("click", closeEmail);
  el.emailClose.addEventListener("click", closeEmail);
  el.emailAnother.addEventListener("click", () => showEmailForm(""));

  el.emailForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const address = el.emailInput.value.trim();
    if (!EMAIL_PATTERN.test(address)) {
      el.emailError.textContent = "Please type a full email address, like name@example.com";
      return;
    }
    el.emailSend.disabled = true;
    try {
      const session = await api(sessionUrl("/email"), { address: address });
      const email = session.emails[session.emails.length - 1];
      seen.emails[email.id] = email.state;
      Sound.ding();
      showEmailConfirmation(address);
      renderSession(session);
    } catch (err) {
      el.emailError.textContent = err.message;
    } finally {
      el.emailSend.disabled = false;
    }
  });

  // The list under the buttons. Sending, retrying and waiting for the
  // internet all look the same to a guest: it's on its way.
  const EMAIL_LIST_TEXT = { sent: "Sent", failed: "Didn't send" };

  let emailListKey = "";

  function renderEmails(emails) {
    // Only redrawn when something changed, so nothing flickers.
    const shown = (email) => (email.state === "sent" || email.state === "failed" ? email.state : "pending");
    const key = JSON.stringify(emails.map((email) => [email.id, shown(email)]));
    if (key !== emailListKey) {
      emailListKey = key;
      el.emailList.innerHTML = "";
      emails.forEach((email) => {
        const item = document.createElement("li");
        item.className = "is-" + shown(email);
        item.innerHTML = '<span class="dot"></span><span class="to"></span><span class="state"></span>';
        item.querySelector(".to").textContent = email.to;
        item.querySelector(".state").textContent = EMAIL_LIST_TEXT[email.state] || "On its way";
        el.emailList.appendChild(item);
      });
    }
    el.emailLabel.textContent = emails.length ? "Email again" : "Email";

    // A guest still at the booth hears about a bad address.
    emails.forEach((email) => {
      const previous = seen.emails[email.id];
      seen.emails[email.id] = email.state;
      if (previous !== undefined && previous !== email.state && email.state === "failed") {
        showToast("Couldn't send to " + email.to + ". Please check the address.", 6);
      }
    });
  }

  // ===========================================================================
  // 11. Printing
  // ===========================================================================
  el.btnPrint.addEventListener("click", async () => {
    el.btnPrint.disabled = true;
    try {
      renderSession(await api(sessionUrl("/print"), {}));
    } catch (err) {
      showToast(err.message);
      el.btnPrint.disabled = false;
    }
  });

  function renderPrint(print) {
    if (print.state === "printing") {
      el.printLabel.textContent = "Printing…";
      el.btnPrint.disabled = true;
    } else {
      el.printLabel.textContent = print.remaining <= 0 ? "Printed ✓"
        : print.state === "done" ? "Print another" : "Print";
      el.btnPrint.disabled = print.remaining <= 0;
    }
    if (print.state !== seen.print) {
      if (seen.print === "printing" && print.state === "done") {
        showToast("Printing. Your strip will be ready in a moment.", 5);
      }
      if (seen.print === "printing" && print.state === "failed") {
        showToast(print.message || "The printer has a problem. Please ask for help.", 6);
      }
      seen.print = print.state;
    }
  }

  // ===========================================================================
  // 12. Status checks and start-up
  // ===========================================================================
  let piReachable = true;
  let serverId = null;
  let contentVersion = null;

  async function checkStatus() {
    try {
      const status = await api("/api/status");

      // The app on the Pi restarted (e.g. after ./deploy.sh). Reload to get
      // the newest version, but never in the middle of someone's session.
      if (serverId !== null && status.server_id !== serverId) {
        state.reloadPending = true;
        if (state.screen === "start" && !state.busy) window.location.reload();
      }
      serverId = status.server_id;

      // Frames or designs changed on the admin page: rebuild the buttons
      // (straight away on the start screen, otherwise when the guests are done).
      if (contentVersion !== null && status.content_version !== contentVersion) {
        state.looksPending = true;
        if (state.screen === "start" && !state.busy) refreshLooks();
      }
      contentVersion = status.content_version;

      if (!piReachable) {
        piReachable = true;
        if (activeImage()) startStream();
      }

      // A missing camera only matters on the live screen.
      if (!status.camera.connected && state.screen !== "review") {
        showNotice("Camera not connected",
          "Check the camera's USB cable. The booth will reconnect by itself as soon as it's back.");
      } else {
        hideNotice();
      }
    } catch (err) {
      piReachable = false;
      showNotice("Can't reach the photo booth",
        "Make sure this iPad is on the same Wi-Fi as the booth. Trying again…");
    } finally {
      setTimeout(checkStatus, 2000);
    }
  }

  el.startTitle.textContent = CFG.title;
  el.startSub.textContent = "Three photos · one strip";
  setupPicker(el.filterGrid, el.frameRow, selectFilter, selectOverlay);
  buildEditor();
  fillLooks();
  showStart();
  checkStatus();
})();
