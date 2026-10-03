/* =============================================================================
   Admin page: talks to the JSON API in admin.py.
   One section per tab: Dashboard, Gallery, Emails, Settings, Frames,
   Strip designs, System, plus making frames and designs with an AI model.
   ============================================================================= */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const boothUrl = location.protocol + "//" + location.hostname + ":" + document.body.dataset.boothPort;

  // --- Helpers ----------------------------------------------------------------

  async function api(path, body, isForm) {
    const options = { cache: "no-store", headers: {} };
    if (body !== undefined) {
      options.method = "POST";
      if (isForm) {
        options.body = body;
      } else {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (err) {
      throw new Error("Can't reach the booth.");
    }
    let data = {};
    try { data = await response.json(); } catch (err) { /* not JSON */ }
    if (!response.ok || data.ok === false) throw new Error(data.error || "Request failed (" + response.status + ")");
    return data;
  }

  let toastTimer;
  function toast(message, isError) {
    const el = $("toast");
    el.textContent = message;
    el.classList.toggle("is-error", !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3500);
  }

  /** Run an API call from a button: disable it meanwhile, toast the result. */
  async function action(button, promise, success) {
    if (button) button.disabled = true;
    try {
      const result = await promise;
      if (success !== false) toast(success || result.message || "Done ✓");
      return result;
    } catch (err) {
      toast(err.message, true);
      return null;
    } finally {
      if (button) button.disabled = false;
    }
  }

  /** Build an element: h("div", {class: "x"}, "text", child...). */
  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === false || value == null) return;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    children.flat().forEach((child) => {
      if (child == null || child === false) return;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function facts(container, rows) {
    container.innerHTML = "";
    rows.forEach(([label, value]) => {
      container.append(h("dt", null, label), h("dd", null, value));
    });
  }

  const pill = (text, kind) => h("span", { class: "pill " + kind }, text);

  function duration(seconds) {
    if (seconds == null) return "–";
    if (seconds < 60) return seconds + "s";
    const d = Math.floor(seconds / 86400), hrs = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
    return (d ? d + "d " : "") + (d || hrs ? hrs + "h " : "") + m + "m";
  }

  const niceTime = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

  // --- Tabs ---------------------------------------------------------------------

  const loaders = {};
  let currentTab = "dashboard";

  function showTab(name) {
    currentTab = name;
    if (name !== "dashboard") stopDashboardStream();
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === name));
    document.querySelectorAll(".tab-page").forEach((page) => { page.hidden = page.id !== "tab-" + name; });
    history.replaceState(null, "", "#" + name);
    if (loaders[name]) loaders[name]();
  }

  document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("open-booth").href = boothUrl;

  // =============================================================================
  // Dashboard
  // =============================================================================
  loaders.dashboard = async function () {
    let data;
    try { data = await api("/api/overview"); } catch (err) { toast(err.message, true); return; }
    const c = data.counts, cam = data.booth.camera, pi = data.pi;

    $("dash-title").textContent = data.event.title || "Dashboard";
    $("dash-sub").textContent = "Booth at " + boothUrl.replace(/^https?:\/\//, "");

    const stats = $("dash-stats");
    stats.innerHTML = "";
    [[c.strips_today, "strips today"], [c.strips_total, "strips in total"],
     [c.emails_today, "emails sent today"], [c.emails_waiting, "emails waiting"],
     [c.emails_failed, "emails failed"]].forEach(([n, label]) => {
      stats.append(h("div", { class: "stat" }, h("b", null, n), h("span", null, label)));
    });

    facts($("dash-camera"), [
      ["Status", cam.connected ? pill("Connected", "good") : pill(cam.error || "Offline", "bad")],
      ["Resolution", cam.resolution || "–"],
      ["Frame rate", cam.connected ? cam.fps + " fps" : "–"],
    ]);

    const email = data.email;
    facts($("dash-booth"), [
      ["Email", !email.enabled ? pill("Switched off", "off")
        : email.configured ? pill("Sending from " + email.address, "good")
        : pill("Not set up: add Gmail in Settings", "warn")],
      ["Internet", data.booth.queue.online === false ? pill("Offline (emails are waiting)", "warn")
        : data.booth.queue.online ? pill("Online", "good") : pill("Not checked yet", "off")],
      ["Printing", data.print.enabled ? pill("On: " + data.print.printer, "good") : pill("Off", "off")],
      ["Email design", email.template],
      ["Strip design", data.event.theme],
    ]);

    facts($("dash-pi"), [
      ["Temperature", pi.temperature == null ? "–" :
        pill(pi.temperature + " °C", pi.temperature > 75 ? "bad" : pi.temperature > 65 ? "warn" : "good")],
      ["Power & heat", pi.power_ok == null ? "–" : pi.power_ok ? pill("No problems since switch-on", "good")
        : h("div", null, pi.problems.map((p) => h("div", null, pill(p, "bad"))))],
      ["Storage free", pi.disk_free_gb + " GB (" + pi.disk_used_pct + "% used)"],
      ["Address", pi.ip],
      ["Restarts since switch-on", pi.app_restarts == null ? "–" : pi.app_restarts > 2
        ? pill(pi.app_restarts + ". It keeps crashing: see System → Log", "bad") : pi.app_restarts],
    ]);
    startDashboardStream();
  };

  // Live camera on the dashboard. The stream only runs while the dashboard is
  // showing, so the Pi isn't sending video nobody is watching.
  const snapshot = $("dash-snapshot");

  function startDashboardStream() {
    if (currentTab !== "dashboard" || document.hidden || snapshot.dataset.live === "1") return;
    snapshot.dataset.live = "1";
    $("dash-snapshot-msg").textContent = "";
    snapshot.src = "/api/stream.mjpg?t=" + Date.now();
  }

  function stopDashboardStream() {
    snapshot.dataset.live = "";
    snapshot.removeAttribute("src");
  }

  snapshot.addEventListener("error", () => {
    snapshot.dataset.live = "";
    $("dash-snapshot-msg").textContent = "No camera picture";
    setTimeout(startDashboardStream, 3000);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopDashboardStream(); else startDashboardStream();
  });

  setInterval(() => { if (currentTab === "dashboard" && !document.hidden) loaders.dashboard(); }, 10000);

  // =============================================================================
  // Gallery
  // =============================================================================
  let galleryOffset = 0;

  loaders.gallery = function () {
    galleryOffset = 0;
    $("gallery-grid").innerHTML = "";
    loadGallery();
  };

  async function loadGallery() {
    let data;
    try { data = await api("/api/gallery?offset=" + galleryOffset); } catch (err) { toast(err.message, true); return; }
    $("gallery-sub").textContent = data.total + " strip" + (data.total === 1 ? "" : "s");
    if (!data.total) {
      $("gallery-grid").append(h("div", { class: "empty" }, "No strips yet. They'll appear here as guests take them."));
    }
    data.items.forEach((item) => {
      $("gallery-grid").append(h("button", { class: "gallery-item", onclick: () => openStrip(item) },
        h("img", { src: "/thumbs/" + item.id + ".jpg", alt: "", loading: "lazy" }),
        h("span", null, niceTime(item.time))));
    });
    galleryOffset += data.items.length;
    $("gallery-more").hidden = galleryOffset >= data.total;
  }

  $("gallery-more").addEventListener("click", loadGallery);

  let openItem = null;

  function openStrip(item) {
    openItem = item;
    $("strip-big").src = "/files/strips/" + item.id + "_strip.jpg?t=" + Date.now();
    $("strip-title").textContent = niceTime(item.time);
    $("strip-download").href = "/files/strips/" + item.id + "_strip.jpg?download=1";
    const photos = $("strip-photos");
    photos.innerHTML = "";
    item.photos.forEach((name, i) => {
      photos.append(h("a", { href: "/files/photos/" + name + "?download=1", title: "Download photo " + (i + 1) },
        h("img", { src: "/files/photos/" + name, alt: "Photo " + (i + 1) })));
    });
    $("strip-email").value = "";
    $("strip-modal").hidden = false;
  }

  $("strip-close").addEventListener("click", () => { $("strip-modal").hidden = true; });
  $("strip-modal").addEventListener("click", (e) => { if (e.target.id === "strip-modal") $("strip-modal").hidden = true; });

  $("strip-email-form").addEventListener("submit", (e) => {
    e.preventDefault();
    action(e.submitter, api("/api/gallery/" + openItem.id + "/email", { address: $("strip-email").value.trim() }));
  });
  $("strip-print").addEventListener("click", (e) => action(e.currentTarget, api("/api/gallery/" + openItem.id + "/print", {})));
  $("strip-delete").addEventListener("click", async (e) => {
    if (!confirm("Delete this strip and its photos from the Pi? This can't be undone.")) return;
    if (await action(e.currentTarget, api("/api/gallery/" + openItem.id + "/delete", {}), "Deleted")) {
      $("strip-modal").hidden = true;
      loaders.gallery();
    }
  });

  // =============================================================================
  // Emails
  // =============================================================================
  loaders.emails = async function () {
    let data;
    try { data = await api("/api/emails"); } catch (err) { toast(err.message, true); return; }
    $("emails-sub").textContent = data.online === false ? "The booth is offline: waiting emails go out when it's back." : "";

    const table = (rows, columns, empty) => rows.length
      ? h("table", { class: "table" }, h("tr", null, columns.map((c) => h("th", null, c.title))),
          rows.map((row) => h("tr", null, columns.map((c) => h("td", { class: c.cls }, c.value(row))))))
      : h("p", { class: "muted" }, empty);

    const jobButtons = (job, retry) => [
      retry && h("button", { class: "btn btn-small", onclick: (e) => action(e.currentTarget,
        api("/api/emails/" + job.id + "/retry", {})).then(loaders.emails) }, "Retry"),
      " ",
      h("button", { class: "btn btn-small btn-danger", onclick: (e) => action(e.currentTarget,
        api("/api/emails/" + job.id + "/delete", {}), "Removed").then(loaders.emails) }, "Remove"),
    ];

    $("emails-waiting").replaceChildren(table(data.waiting, [
      { title: "To", value: (j) => j.to },
      { title: "Tries", value: (j) => j.attempts },
      { title: "Last problem", value: (j) => j.error || "–" },
      { title: "", cls: "actions", value: (j) => jobButtons(j, false) },
    ], "Nothing waiting."));

    $("emails-failed").replaceChildren(table(data.failed, [
      { title: "To", value: (j) => j.to },
      { title: "Why", value: (j) => j.error || "–" },
      { title: "", cls: "actions", value: (j) => jobButtons(j, true) },
    ], "No failed emails."));

    $("emails-sent").replaceChildren(table(data.sent, [
      { title: "When", value: (e) => niceTime(e.time) },
      { title: "To", value: (e) => e.to },
    ], "No emails sent yet."));
  };

  $("emails-retry").addEventListener("click", (e) => action(e.currentTarget, api("/api/emails/retry", {})).then(() => setTimeout(loaders.emails, 3000)));

  // =============================================================================
  // Settings
  // =============================================================================
  let settingsLoaded = false;
  let savedValues = {};      // what the form showed when it loaded

  function formValues() {
    const values = {};
    $("settings-form").querySelectorAll("[name]").forEach((input) => {
      values[input.name] = input.type === "checkbox" ? input.checked : input.value;
    });
    return values;
  }

  loaders.settings = async function () {
    if (settingsLoaded) return;
    let data;
    try { data = await api("/api/settings"); } catch (err) { toast(err.message, true); return; }
    const form = $("settings-form");
    form.innerHTML = "";

    data.groups.forEach((group) => {
      const card = h("div", { class: "card" }, h("h2", null, group.title));
      group.fields.forEach((f) => {
        const id = "set-" + f.key;
        let input;
        if (f.type === "bool") {
          input = h("label", { class: "switch" },
            h("input", { type: "checkbox", id: id, name: f.key, checked: !!f.value }), h("span"));
        } else if (f.type === "select") {
          input = h("select", { id: id, name: f.key },
            f.options.map((o) => h("option", { value: o.value, selected: o.value === f.value }, o.label)));
        } else if (f.type === "textarea") {
          input = h("textarea", { id: id, name: f.key }, f.value);
        } else {
          input = h("input", {
            id: id, name: f.key,
            type: f.type === "secret" ? "password" : f.type === "number" ? "number" : "text",
            step: f.type === "number" ? "any" : null,
            value: f.type === "secret" ? "" : f.value,
            placeholder: f.type === "secret" ? (f.is_set ? "•••••••• (set; type to change)" : "Not set") : null,
            autocomplete: "off",
          });
        }
        card.append(h("div", { class: "field" }, h("label", { for: id }, f.label),
          h("div", null, input), f.help ? h("p", { class: "help" }, f.help) : null));
      });
      form.append(card);
    });

    // Email design buttons + preview.
    const select = $("set-EMAIL_TEMPLATE");
    const picker = $("design-picker");
    picker.innerHTML = "";
    Array.from(select.options).forEach((option) => {
      picker.append(h("button", { class: "btn btn-small", type: "button", "data-id": option.value, onclick: () => {
        select.value = option.value;
        previewDesign(option.value);
      } }, option.textContent));
    });
    select.addEventListener("change", () => previewDesign(select.value));
    previewDesign(select.value);
    savedValues = formValues();
    settingsLoaded = true;
  };

  function previewDesign(id) {
    $("email-frame").src = "/api/email-preview/" + encodeURIComponent(id);
    document.querySelectorAll("#design-picker .btn").forEach((b) => {
      b.classList.toggle("btn-primary", b.dataset.id === id);
    });
  }

  $("settings-save").addEventListener("click", async (e) => {
    // Only send what was changed, so untouched settings keep following the defaults.
    const values = {};
    Object.entries(formValues()).forEach(([key, value]) => {
      if (value !== savedValues[key]) values[key] = value;
    });
    const result = await action(e.currentTarget, api("/api/settings", { values: values }), false);
    if (!result) return;
    if (!result.restarting) { toast("Nothing changed."); return; }
    toast("Saved. Restarting the booth…");
    settingsLoaded = false;
    await waitForRestart();
    toast("Saved and restarted ✓");
    loaders.settings();
  });

  /** Wait until the booth has gone away and come back after a restart. */
  async function waitForRestart() {
    await new Promise((r) => setTimeout(r, 2500));
    for (let i = 0; i < 30; i++) {
      try { await api("/api/overview"); return; } catch (err) { await new Promise((r) => setTimeout(r, 1000)); }
    }
    toast("The booth hasn't come back yet. Check the System tab or ./deploy.sh logs.", true);
  }

  $("test-email-form").addEventListener("submit", (e) => {
    e.preventDefault();
    action(e.submitter, api("/api/email-test", { address: $("test-email").value.trim() }));
  });

  // =============================================================================
  // Frames: upload, reorder, rename, hide, delete
  // =============================================================================

  /** Move item `index` of `ids` up (-1) or down (+1) and save the new order. */
  async function move(ids, index, step, url, reload) {
    const target = index + step;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    await action(null, api(url, { ids: ids }), false);
    reload();
  }

  loaders.frames = async function () {
    let data;
    try { data = await api("/api/frames"); } catch (err) { toast(err.message, true); return; }
    const list = $("frames-list");
    list.innerHTML = "";
    if (!data.frames.length) list.append(h("div", { class: "empty" }, "No frames yet. Upload a transparent PNG."));
    const ids = data.frames.map((f) => f.id);
    const firstShown = data.frames.findIndex((f) => !f.hidden);

    data.frames.forEach((f, index) => {
      const url = (verb) => "/api/frames/" + encodeURIComponent(f.id) + "/" + verb;
      const nameInput = h("input", { type: "text", value: f.name, maxlength: 24, "aria-label": "Name" });
      const saveName = () => {
        if (nameInput.value.trim() === f.name) return;
        action(null, api(url("rename"), { name: nameInput.value }), "Renamed").then(loaders.frames);
      };
      nameInput.addEventListener("change", saveName);
      nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") nameInput.blur(); });

      list.append(h("div", { class: "row" + (f.hidden ? " is-hidden" : "") },
        h("div", { class: "order" },
          h("button", { class: "btn btn-small", title: "Move up", disabled: index === 0,
            onclick: () => move(ids, index, -1, "/api/frames/order", loaders.frames) }, "↑"),
          h("button", { class: "btn btn-small", title: "Move down", disabled: index === ids.length - 1,
            onclick: () => move(ids, index, 1, "/api/frames/order", loaders.frames) }, "↓")),
        h("div", { class: "frame-preview" }, f.none ? h("div", { class: "frame-none" }, "Just the photo")
          : h("img", { src: "/overlays/" + encodeURIComponent(f.id) + ".png?t=" + Date.now(), alt: "" })),
        h("div", { class: "row-main" }, nameInput,
          h("span", { class: "tag" }, (f.none ? "No frame" : f.uploaded ? "Uploaded" : "Built-in")
            + (f.hidden ? " · hidden from guests" : index === firstShown ? " · guests start with this" : ""))),
        h("div", { class: "row-actions" },
          h("button", { class: "btn btn-small", onclick: (e) => action(e.currentTarget, api(url(f.hidden ? "show" : "hide"), {}),
            f.hidden ? "Shown to guests" : "Hidden from guests").then(loaders.frames) }, f.hidden ? "Show" : "Hide"),
          f.uploaded && h("button", { class: "btn btn-small btn-danger", onclick: (e) => {
            if (confirm("Delete the frame “" + f.name + "”?")) {
              action(e.currentTarget, api(url("delete"), {}), "Deleted").then(loaders.frames);
            }
          } }, "Delete"))));
    });
  };

  $("frame-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    await action(null, api("/api/frames", form, true), "Frame uploaded. It's on the iPad now.");
    e.target.value = "";
    loaders.frames();
  });

  // =============================================================================
  // Strip designs: edit, create, duplicate, reorder, hide, reset
  // =============================================================================
  let designOptions = { fonts: [], decorations: [] };

  loaders.designs = async function () {
    let data;
    try { data = await api("/api/themes"); } catch (err) { toast(err.message, true); return; }
    designOptions = data;
    const grid = $("designs-grid");
    grid.innerHTML = "";
    const ids = data.themes.map((t) => t.id);
    const stamp = Date.now();

    data.themes.forEach((t, index) => {
      const url = (verb) => "/api/themes/" + encodeURIComponent(t.id) + "/" + verb;
      const tag = t.builtin ? (t.edited ? "Built-in, edited" : "Built-in") : "Your design";
      grid.append(h("div", { class: "design-card" + (t.hidden ? " is-hidden" : "") },
        h("img", { class: "design-thumb", src: url("preview.jpg") + "?t=" + stamp, alt: "", loading: "lazy" }),
        h("div", { class: "design-info" },
          h("div", { class: "name" }, t.name),
          h("span", { class: "tag" }, tag + (t.hidden ? " · hidden" : "")),
          h("div", { class: "button-row" },
            h("button", { class: "btn btn-small", onclick: () => openDesign(t, false) }, "Edit"),
            h("button", { class: "btn btn-small", onclick: () => openDesign(Object.assign({}, t, { name: t.name + " copy" }), true) }, "Duplicate"),
            h("button", { class: "btn btn-small", onclick: (e) => action(e.currentTarget, api(url(t.hidden ? "show" : "hide"), {}),
              t.hidden ? "Shown to guests" : "Hidden from guests").then(loaders.designs) }, t.hidden ? "Show" : "Hide")),
          h("div", { class: "button-row" },
            h("button", { class: "btn btn-small", title: "Move earlier", disabled: index === 0,
              onclick: () => move(ids, index, -1, "/api/themes/order", loaders.designs) }, "←"),
            h("button", { class: "btn btn-small", title: "Move later", disabled: index === ids.length - 1,
              onclick: () => move(ids, index, 1, "/api/themes/order", loaders.designs) }, "→"),
            (t.edited || !t.builtin) && h("button", { class: "btn btn-small btn-danger", onclick: (e) => {
              const question = t.builtin ? "Reset “" + t.name + "” to how it came?" : "Delete “" + t.name + "”?";
              if (confirm(question)) action(e.currentTarget, api(url("delete"), {}), t.builtin ? "Reset" : "Deleted").then(loaders.designs);
            } }, t.builtin ? "Reset" : "Delete")))));
    });
  };

  // --- The design editor ------------------------------------------------------------
  let editingId = null;            // id being edited, or null for a new design
  let previewTimer = null;
  let previewUrl = null;

  const DESIGN_FIELDS = [
    { key: "name", label: "Name", type: "text" },
    { key: "background", label: "Background", type: "color" },
    { key: "text", label: "Title colour", type: "color" },
    { key: "accent", label: "Date colour", type: "color", help: "Also used for decorations." },
    { key: "title_font", label: "Title font", type: "font" },
    { key: "title_size", label: "Title size", type: "range", min: 24, max: 160 },
    { key: "title_spacing", label: "Title letter spacing", type: "range", min: 0, max: 30 },
    { key: "upper", label: "Title in capitals", type: "check" },
    { key: "date_font", label: "Date font", type: "font" },
    { key: "date_size", label: "Date size", type: "range", min: 12, max: 70 },
    { key: "date_spacing", label: "Date letter spacing", type: "range", min: 0, max: 30 },
    { key: "date_color", label: "Date uses", type: "select", options: [["accent", "Date colour"], ["text", "Title colour"]] },
    { key: "decor", label: "Decoration", type: "decor", help: "Drawn under any artwork." },
    { key: "art", label: "Artwork", type: "art" },
  ];

  async function openDesign(theme, asNew) {
    if (!designOptions.fonts.length) designOptions = await api("/api/themes");
    editingId = asNew ? null : theme.id;
    $("design-modal-title").textContent = asNew ? "New design" : "Edit “" + theme.name + "”";
    const form = $("design-form");
    form.innerHTML = "";
    DESIGN_FIELDS.forEach((f) => {
      const value = theme[f.key];
      let input;
      if (f.type === "art") {
        // Artwork comes from "Make one with AI"; here it can only be kept or removed.
        const hasArt = !!(theme.art_back || theme.art_front);
        form.append(h("input", { type: "hidden", name: "art_back", value: theme.art_back || "" }),
                    h("input", { type: "hidden", name: "art_front", value: theme.art_front || "" }));
        if (!hasArt) return;
        form.append(h("div", { class: "field" }, h("label", null, f.label),
          h("div", null, h("label", { class: "switch" }, h("input", { type: "checkbox", name: "use_art", checked: true }), h("span"))),
          h("p", { class: "help" }, "Drawn by the AI. Switch off to remove it from this design.")));
        return;
      }
      if (f.type === "font" || f.type === "decor" || f.type === "select") {
        const options = f.type === "font" ? designOptions.fonts.map((o) => [o.key, o.label])
          : f.type === "decor" ? designOptions.decorations.map((o) => [o.key, o.label]) : f.options;
        input = h("select", { name: f.key }, options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
      } else if (f.type === "check") {
        input = h("label", { class: "switch" }, h("input", { type: "checkbox", name: f.key, checked: !!value }), h("span"));
      } else if (f.type === "range") {
        const out = h("output", null, value);
        const range = h("input", { type: "range", name: f.key, min: f.min, max: f.max, value: value });
        range.addEventListener("input", () => { out.textContent = range.value; });
        input = h("div", { class: "range" }, range, out);
      } else {
        input = h("input", { type: f.type, name: f.key, value: value, maxlength: f.type === "text" ? 30 : null });
      }
      form.append(h("div", { class: "field" }, h("label", null, f.label), h("div", null, input),
        f.help ? h("p", { class: "help" }, f.help) : null));
    });
    form.oninput = schedulePreview;
    form.onchange = schedulePreview;
    $("design-modal").hidden = false;
    renderPreview();
  }

  function designFromForm() {
    const theme = {};
    $("design-form").querySelectorAll("[name]").forEach((input) => {
      theme[input.name] = input.type === "checkbox" ? input.checked : input.value;
    });
    if (theme.use_art === false) theme.art_back = theme.art_front = "";
    delete theme.use_art;
    return theme;
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, 250);
  }

  async function renderPreview() {
    try {
      const response = await fetch("/api/themes/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: designFromForm() }),
      });
      if (!response.ok) return;           // e.g. name empty: keep the last preview
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(await response.blob());
      $("design-preview").src = previewUrl;
    } catch (err) { /* offline for a moment */ }
  }

  function closeDesign() { $("design-modal").hidden = true; }

  $("design-close").addEventListener("click", closeDesign);
  $("design-cancel").addEventListener("click", closeDesign);
  $("design-save").addEventListener("click", async (e) => {
    const target = editingId ? encodeURIComponent(editingId) : "new";
    const result = await action(e.currentTarget, api("/api/themes/" + target, { theme: designFromForm() }));
    if (result) { closeDesign(); loaders.designs(); }
  });
  $("design-new").addEventListener("click", () => {
    openDesign(Object.assign({}, { name: "New design", background: "#ffffff", text: "#1d1d1f", accent: "#d8342c",
      title_font: "bebas", title_size: 96, title_spacing: 4, upper: true, date_font: "fredoka", date_size: 26,
      date_spacing: 4, date_color: "accent", decor: "none" }), true);
  });

  // =============================================================================
  // Make a frame or strip design with an AI model
  //   1. describe it, copy the prompt, paste it into any AI chat;
  //   2. paste the reply back. Frames come back as SVG code, which this page
  //      draws into a transparent 1080 x 960 PNG and uploads. Designs come back
  //      as JSON settings (checked by the Pi) plus SVG artwork, which this page
  //      draws into 600 x 1800 PNGs; they then open in the design editor.
  // =============================================================================
  const FRAME_W = 1080, FRAME_H = 960;
  const STRIP_W = 600, STRIP_H = 1800;
  let aiKind = "frame";      // which button opened the box
  let replyKind = "frame";   // what the pasted reply turned out to be
  let aiFrame = null;        // the drawn frame as a PNG blob, ready to upload
  let aiTimer = null;

  const AI_TEXT = {
    frame: { title: "Make a frame with AI", button: "Add frame",
      placeholder: "What should the frame look like? e.g. “Gold art deco corners and a banner at the bottom " +
                   "saying Happy New Year 2027, for an elegant party”" },
    design: { title: "Make a strip design with AI", button: "Open in the editor",
      placeholder: "What should the strip look like? e.g. “Halloween party: black background, orange title, " +
                   "spooky but fun”. Ask for several to get a choice." },
  };

  function openAi(kind) {
    aiKind = kind;
    replyKind = kind;
    aiFrame = null;
    const text = AI_TEXT[kind];
    $("ai-title").textContent = text.title;
    $("ai-describe").placeholder = text.placeholder;
    $("ai-import").textContent = text.button;
    $("ai-import").disabled = true;
    $("ai-reply").value = "";
    $("ai-prompt").value = "";
    $("ai-frame-name").value = "";
    $("ai-frame-preview").hidden = true;
    $("ai-frame-only").hidden = kind !== "frame";
    aiMessage("");
    $("ai-modal").hidden = false;
    $("ai-describe").focus();
  }

  function aiMessage(text, kind) {
    const el = $("ai-message");
    el.textContent = text;
    el.className = "ai-message" + (kind ? " is-" + kind : "");
  }

  $("frame-ai").addEventListener("click", () => openAi("frame"));
  $("design-ai").addEventListener("click", () => openAi("design"));
  $("ai-close").addEventListener("click", () => { $("ai-modal").hidden = true; });

  async function buildPrompt() {
    const data = await api("/api/ai-prompt", { kind: aiKind, description: $("ai-describe").value });
    $("ai-prompt").value = data.prompt;
    return data.prompt;
  }

  $("ai-describe").addEventListener("change", () => buildPrompt().catch(() => {}));
  $("ai-modal").querySelector(".ai-details").addEventListener("toggle", () => buildPrompt().catch(() => {}));

  $("ai-copy").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    let prompt;
    try { prompt = await buildPrompt(); } catch (err) { toast(err.message, true); return; }
    if (await copyText(prompt)) {
      button.textContent = "Copied ✓";
      setTimeout(() => { button.textContent = "Copy prompt"; }, 2000);
    } else {
      // Copying can be blocked; show the prompt so it can be copied by hand.
      $("ai-modal").querySelector(".ai-details").open = true;
      $("ai-prompt").select();
      toast("Couldn't copy automatically. The prompt is selected: copy it with ⌘C / Ctrl+C.", true);
    }
  });

  /** Copy text to the clipboard. The admin page is plain http, where the modern clipboard API is missing. */
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch (err) { /* try the old way */ }
    }
    const area = h("textarea", { readonly: true, style: "position:fixed;top:0;left:0;opacity:0" });
    area.value = text;
    document.body.append(area);
    area.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (err) { /* not allowed */ }
    area.remove();
    return copied;
  }

  $("ai-reply").addEventListener("input", () => {
    clearTimeout(aiTimer);
    aiTimer = setTimeout(checkReply, 300);
  });

  async function checkReply() {
    const reply = $("ai-reply").value.trim();
    aiFrame = null;
    $("ai-import").disabled = true;
    $("ai-frame-preview").hidden = true;
    // Work out what the reply is, whichever button opened the box: a strip
    // design has JSON settings (and maybe SVG artwork), a frame is only SVG.
    const hasSettings = /"(background|title_font)"\s*:/.test(reply);
    const isSvg = /<svg[\s>]/i.test(reply);
    replyKind = hasSettings ? "design" : isSvg ? "frame" : /[{[]/.test(reply) ? "design" : aiKind;
    $("ai-import").textContent = AI_TEXT[replyKind].button;
    $("ai-frame-only").hidden = replyKind !== "frame";
    if (!reply) { aiMessage(""); return; }
    const mixedUp = replyKind !== aiKind
      ? (replyKind === "design" ? "This reply is a strip design, not a frame. " : "This reply is a frame, not a strip design. ")
      : "";
    if (replyKind === "design") {
      $("ai-import").disabled = false;
      aiMessage(mixedUp + (mixedUp ? "You can still add it: it goes into Strip designs." : ""), mixedUp ? "warn" : null);
      return;
    }
    try {
      const canvas = await drawSvg(svgFromReply(reply));
      const warning = coverageWarning(canvas);
      aiFrame = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!aiFrame) throw new Error("The browser couldn't turn the frame into a picture.");
      $("ai-frame-preview").src = URL.createObjectURL(aiFrame);
      $("ai-frame-preview").hidden = false;
      $("ai-import").disabled = false;
      if (!$("ai-frame-name").value) $("ai-frame-name").value = frameNameFrom(reply);
      aiMessage(mixedUp + (warning || "Looks good. Check the preview, name it, then add it."),
        warning || mixedUp ? "warn" : null);
    } catch (err) {
      aiMessage(err.message, "error");
    }
  }

  /** Pull the SVG out of an AI reply and make sure a browser can draw it at the frame size. */
  function svgFromReply(text, width, height) {
    width = width || FRAME_W;
    height = height || FRAME_H;
    const start = text.search(/<svg[\s>]/i);
    const end = text.toLowerCase().lastIndexOf("</svg>");
    if (start < 0 || end < start) {
      throw new Error("Couldn't find the frame's code in that reply. It should start with <svg and end with </svg>. " +
                      "If the reply was cut off, ask the AI to send the whole thing again.");
    }
    let code = text.slice(start, end + 6);
    const openTag = code.slice(0, code.indexOf(">"));
    if (!/xmlns\s*=/.test(openTag)) code = code.replace(/^<svg/i, '<svg xmlns="http://www.w3.org/2000/svg"');
    if (/xlink:/.test(code) && !/xmlns:xlink\s*=/.test(openTag)) {
      code = code.replace(/^<svg/i, '<svg xmlns:xlink="http://www.w3.org/1999/xlink"');
    }
    const doc = new DOMParser().parseFromString(code, "image/svg+xml");
    const svg = doc.documentElement;
    if (doc.querySelector("parsererror") || svg.nodeName.toLowerCase() !== "svg") {
      throw new Error("The frame's code is broken or incomplete. Ask the AI to check it and send it again.");
    }
    if (!svg.getAttribute("viewBox")) {
      const w = parseFloat(svg.getAttribute("width")) || width, ht = parseFloat(svg.getAttribute("height")) || height;
      svg.setAttribute("viewBox", "0 0 " + w + " " + ht);
    }
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("preserveAspectRatio", "none");    // fill the whole picture, like an uploaded PNG
    return new XMLSerializer().serializeToString(svg);
  }

  /** Draw SVG code onto a transparent canvas (frame size unless told otherwise). */
  function drawSvg(code, width, height) {
    width = width || FRAME_W;
    height = height || FRAME_H;
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = h("canvas", { width: width, height: height });
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        resolve(canvas);
      };
      img.onerror = () => reject(new Error("The browser couldn't draw that artwork. Ask the AI to fix the SVG " +
                                           "(and to use only plain shapes and text)."));
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(code);
    });
  }

  /** A warning if the frame is empty, or covers the middle of the photo where faces are. */
  function coverageWarning(canvas) {
    const pixels = canvas.getContext("2d").getImageData(0, 0, FRAME_W, FRAME_H).data;
    let drawn = 0, middle = 0, middleTotal = 0;
    for (let y = 0; y < FRAME_H; y += 4) {
      for (let x = 0; x < FRAME_W; x += 4) {
        const solid = pixels[(y * FRAME_W + x) * 4 + 3] > 128;
        if (solid) drawn++;
        if (x > 270 && x < 810 && y > 240 && y < 720) {
          middleTotal++;
          if (solid) middle++;
        }
      }
    }
    if (!drawn) return "The frame looks empty. Check the preview, or ask the AI for a bolder design.";
    if (middle / middleTotal > 0.2) return "This frame covers the middle of the photo, where faces go. " +
                                           "You can still add it, or ask the AI to keep the centre clear.";
    return null;
  }

  /** A button name from the SVG's <title>, if the AI gave it one. */
  function frameNameFrom(reply) {
    const match = reply.match(/<title>([^<]{1,60})<\/title>/i);
    return match ? match[1].trim().slice(0, 24) : "";
  }

  /** Draw a design's SVG artwork into PNGs, upload them and note their ids on the design. */
  async function addArtwork(design) {
    for (const [svgKey, artKey] of [["svg_back", "art_back"], ["svg_front", "art_front"]]) {
      const code = design[svgKey];
      delete design[svgKey];
      if (!code) continue;
      const canvas = await drawSvg(svgFromReply(code, STRIP_W, STRIP_H), STRIP_W, STRIP_H);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("The browser couldn't turn the artwork into a picture.");
      const form = new FormData();
      form.append("file", blob, "art.png");
      design[artKey] = (await api("/api/strip-art", form, true)).id;
    }
  }

  $("ai-import").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    if (replyKind === "frame") {
      if (!aiFrame) return;
      const name = $("ai-frame-name").value.trim() || "AI frame";
      const form = new FormData();
      form.append("file", aiFrame, "frame.png");
      form.append("name", name);
      if (await action(button, api("/api/frames", form, true), "Frame added. It's on the iPad now.")) {
        $("ai-modal").hidden = true;
        showTab("frames");
      }
      return;
    }
    const result = await action(button, api("/api/themes/import", { text: $("ai-reply").value }), false);
    if (!result) return;
    button.disabled = true;
    aiMessage("Drawing the artwork…");
    try {
      for (const design of result.designs) await addArtwork(design);
    } catch (err) {
      aiMessage(err.message, "error");
      return;
    } finally {
      button.disabled = false;
    }
    $("ai-modal").hidden = true;
    if (currentTab !== "designs") showTab("designs");
    if (result.designs.length === 1) {
      openDesign(result.designs[0], true);     // check the preview, adjust, then save
      return;
    }
    // Several designs: save them all; they can be edited or deleted afterwards.
    button.disabled = true;
    try {
      for (const design of result.designs) await api("/api/themes/new", { theme: design });
      toast(result.designs.length + " designs added. They're on the iPad now.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      loaders.designs();
    }
  });

  // =============================================================================
  // System
  // =============================================================================
  loaders.system = async function () {
    let data;
    try { data = await api("/api/system"); } catch (err) { toast(err.message, true); return; }
    const pi = data.pi;
    facts($("system-pi"), [
      ["Name", pi.hostname],
      ["Address", pi.ip],
      ["Temperature", pi.temperature == null ? "–" : pi.temperature + " °C"],
      ["Power & heat", pi.power_ok == null ? "–" : pi.power_ok ? "No problems since switch-on" : pi.problems.join(", ")],
      ["Storage free", pi.disk_free_gb + " GB (" + pi.disk_used_pct + "% used)"],
      ["Load", pi.load == null ? "–" : pi.load],
      ["Pi running for", duration(pi.uptime_s)],
      ["Software", "Python " + data.python + " · OpenCV " + data.opencv],
    ]);
    $("system-printers").textContent = data.printers;
    loadLogs();
    try {
      const healthLog = await api("/api/health-log?n=240");
      const box = $("system-health");
      box.textContent = healthLog.lines.join("\n") || "(nothing recorded yet)";
      box.scrollTop = box.scrollHeight;
    } catch (err) { /* booth restarting */ }
  };

  async function loadLogs() {
    try {
      const data = await api("/api/logs?n=400");
      const box = $("system-logs");
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.textContent = data.lines.join("\n") || "(empty)";
      if (atBottom) box.scrollTop = box.scrollHeight;
    } catch (err) { /* booth restarting */ }
  }

  setInterval(() => {
    if (currentTab === "system" && $("logs-follow").checked && !document.hidden) loadLogs();
  }, 3000);

  $("printer-test").addEventListener("click", (e) => action(e.currentTarget, api("/api/printer/test", {})));

  document.querySelectorAll("[data-system]").forEach((button) => {
    button.addEventListener("click", async () => {
      const what = button.dataset.system;
      const question = {
        restart: "Restart the booth app? Guests in the middle of a session will lose it.",
        reboot: "Reboot the Raspberry Pi? The booth will be back in about a minute.",
        shutdown: "Shut down the Raspberry Pi? You'll need to unplug and replug it to start it again.",
      }[what];
      if (!confirm(question)) return;
      const result = await action(button, api("/api/system/" + what, {}));
      if (result && what === "restart") { await waitForRestart(); toast("Booth restarted ✓"); }
    });
  });

  // --- Start on the tab in the address (#gallery etc.) ---------------------------
  const first = location.hash.replace("#", "");
  showTab(loaders[first] ? first : "dashboard");
  window.addEventListener("hashchange", () => {
    const tab = location.hash.replace("#", "");
    if (loaders[tab] && tab !== currentTab) {
      document.querySelectorAll(".modal").forEach((m) => { m.hidden = true; });
      showTab(tab);
    }
  });
})();
