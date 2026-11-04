
"use strict";

const STORAGE_KEY = "clipvault.queue.v1";
const SEED_FILE = "halo-60.json";

let clips = [];

const $ = id => document.getElementById(id);

function normaliseSource(value) {
  const input = value.trim();

  if (!input) return "";

  try {
    const url = new URL(input);

    if (!["https:", "http:"].includes(url.protocol)) {
      throw new Error("Unsupported URL");
    }

    return url.href;
  } catch {
    return "https://www.youtube.com/results?search_query=" +
      encodeURIComponent(input);
  }
}

function isYouTubeVideo(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();

    if (host === "youtu.be") {
      return /^[\w-]{11}$/.test(
        parsed.pathname.slice(1)
      );
    }

    if (
      host === "youtube.com" ||
      host === "www.youtube.com" ||
      host === "m.youtube.com"
    ) {
      if (parsed.pathname === "/watch") {
        return /^[\w-]{11}$/.test(
          parsed.searchParams.get("v") || ""
        );
      }

      const match = parsed.pathname.match(
        /^\/(?:shorts|live|embed)\/([\w-]{11})/
      );

      return Boolean(match);
    }

    return false;
  } catch {
    return false;
  }
}

function makeClip(item, index) {
  const name = String(
    item.name || item.title || `Clip ${index + 1}`
  ).slice(0, 180);

  const source = normaliseSource(
    String(item.source || item.url || name)
  );

  const video = item.video && isYouTubeVideo(item.video)
    ? item.video
    : isYouTubeVideo(source)
      ? source
      : "";

  return {
    id: String(item.id || `clip-${index + 1}`),
    name,
    source,
    video,
    done: Boolean(item.done)
  };
}

function save() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(clips)
    );
  } catch {
    alert("Unable to save locally. Export your queue.");
  }
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);

  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;

  return element;
}

function actionButton(label, handler) {
  const button = createElement("button", "secondary", label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function updateStats() {
  $("total").textContent = clips.length;
  $("selected").textContent =
    clips.filter(c => Boolean(c.video)).length;
  $("done").textContent =
    clips.filter(c => c.done).length;
}

function render() {
  updateStats();

  const query = $("filter").value.toLowerCase().trim();
  const status = $("status").value;
  const container = $("items");

  container.replaceChildren();

  const visible = clips.filter(clip => {
    const matchesText = clip.name.toLowerCase().includes(query);

    let matchesStatus = true;

    if (status === "pending") {
      matchesStatus = !clip.video && !clip.done;
    } else if (status === "selected") {
      matchesStatus = Boolean(clip.video) && !clip.done;
    } else if (status === "done") {
      matchesStatus = clip.done;
    }

    return matchesText && matchesStatus;
  });

  if (!visible.length) {
    container.append(
      createElement("p", "hint", "No matching clips.")
    );
    return;
  }

  visible.forEach(clip => {
    const card = createElement("article", "clip");

    const heading = createElement("h3", "", clip.name);
    card.append(heading);

    const statusText = clip.done
      ? "Complete"
      : clip.video
        ? "Video selected"
        : "Needs video";

    card.append(
      createElement("p", "hint", statusText)
    );

    const link = createElement(
      "a",
      "",
      clip.video ? "Open selected video" : "Find video"
    );

    link.href = clip.video || clip.source;
    link.target = "_blank";
    link.rel = "noopener noreferrer";

    card.append(link);

    const input = createElement("input");
    input.type = "url";
    input.placeholder = "Paste exact YouTube video URL";
    input.value = clip.video;

    card.append(input);

    const actions = createElement("div", "actions");

    actions.append(
      actionButton("Save video link", () => {
        const value = input.value.trim();

        if (value && !isYouTubeVideo(value)) {
          alert("Enter a valid YouTube video URL.");
          return;
        }

        clip.video = value;
        clip.done = false;
        save();
        render();
      })
    );

    actions.append(
      actionButton(
        clip.done ? "Mark incomplete" : "Mark complete",
        () => {
          if (!clip.done && !clip.video) {
            alert("Select a video first.");
            return;
          }

          clip.done = !clip.done;
          save();
          render();
        }
      )
    );

    actions.append(
      actionButton("Remove", () => {
        if (!confirm(`Remove "${clip.name}"?`)) return;

        clips = clips.filter(item => item.id !== clip.id);
        save();
        render();
      })
    );

    card.append(actions);
    container.append(card);
  });
}

function downloadFile(filename, contents, mime) {
  const blob = new Blob([contents], { type: mime });
  const url = URL.createObjectURL(blob);

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();

  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportJSON() {
  downloadFile(
    "clipvault-manifest.json",
    JSON.stringify(clips, null, 2),
    "application/json"
  );
}

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportCSV() {
  const rows = [
    ["Name", "Source", "Video", "Complete"],
    ...clips.map(c => [
      c.name,
      c.source,
      c.video,
      c.done ? "Yes" : "No"
    ])
  ];

  const csv = rows.map(
    row => row.map(csvCell).join(",")
  ).join("\r\n");

  downloadFile(
    "clipvault-manifest.csv",
    "\uFEFF" + csv,
    "text/csv;charset=utf-8"
  );
}

async function importJSON(file) {
  try {
    if (file.size > 5 * 1024 * 1024) {
      throw new Error("File exceeds 5 MB.");
    }

    const data = JSON.parse(await file.text());

    if (!Array.isArray(data)) {
      throw new Error("Expected a JSON array.");
    }

    if (data.length > 5000) {
      throw new Error("Maximum 5000 clips.");
    }

    const imported = data.map(makeClip);

    if (!confirm(
      `Replace your queue with ${imported.length} clips?`
    )) return;

    clips = imported;
    save();
    render();
  } catch (error) {
    alert(`Import failed: ${error.message}`);
  }
}

async function initialise() {
  const stored = localStorage.getItem(STORAGE_KEY);

  if (stored !== null) {
    try {
      const data = JSON.parse(stored);
      if (!Array.isArray(data)) throw new Error();

      clips = data.map(makeClip);
    } catch {
      clips = [];
      alert("Saved queue could not be read.");
    }
  } else {
    try {
      const response = await fetch(SEED_FILE, {
        cache: "no-store"
      });

      if (!response.ok) throw new Error("Seed not found");

      const data = await response.json();

      if (!Array.isArray(data)) {
        throw new Error("Invalid seed format");
      }

      clips = data.map(makeClip);
      save();
    } catch {
      clips = [];
      console.warn("Halo seed file unavailable.");
    }
  }

  render();
}

$("add").addEventListener("submit", event => {
  event.preventDefault();

  const name = $("name").value.trim();
  const source = $("source").value.trim();

  if (!name || !source) return;

  clips.unshift(makeClip({
    id: crypto.randomUUID(),
    name,
    source
  }, clips.length));

  save();
  $("add").reset();
  render();
});

$("filter").addEventListener("input", render);
$("status").addEventListener("change", render);

$("export").addEventListener("click", exportJSON);
$("csv").addEventListener("click", exportCSV);

$("importBtn").addEventListener("click", () => {
  $("import").click();
});

$("import").addEventListener("change", async event => {
  const file = event.target.files[0];

  if (file) await importJSON(file);

  event.target.value = "";
});

initialise();
