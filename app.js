
"use strict";

// ClipVault single-video submission interface.
//
// The website validates and copies a video URL,
// then opens the existing GitHub Actions workflow.
//
// No GitHub credentials are stored in the browser.

const GITHUB_OWNER = "KirkJL";
const GITHUB_REPO = "ClipVault";
const WORKFLOW_FILE = "download.yml";

const WORKFLOW_URL =
  `https://github.com/${GITHUB_OWNER}/` +
  `${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}`;

const form = document.getElementById("download-form");
const videoInput = document.getElementById("video-url");
const qualityInput = document.getElementById("quality");
const statusMessage = document.getElementById("status-message");

function normaliseYouTubeUrl(value) {
  let parsed;

  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error("Enter a valid YouTube URL.");
  }

  if (parsed.protocol !== "https:") {
    throw new Error("Only HTTPS URLs are accepted.");
  }

  const host = parsed.hostname.toLowerCase();

  let videoId = "";

  if (host === "youtu.be") {
    videoId = parsed.pathname.slice(1);
  } else if (
    host === "youtube.com" ||
    host === "www.youtube.com" ||
    host === "m.youtube.com"
  ) {
    if (parsed.pathname === "/watch") {
      videoId = parsed.searchParams.get("v") || "";
    } else {
      const match = parsed.pathname.match(
        /^\/(?:shorts|live|embed)\/([\w-]{11})\/?$/
      );

      videoId = match ? match[1] : "";
    }
  }

  if (!/^[\w-]{11}$/.test(videoId)) {
    throw new Error(
      "Paste a direct YouTube video link, not a search page."
    );
  }

  return `https://www.youtube.com/watch?v=${videoId}`;
}

function showStatus(message, type = "") {
  statusMessage.textContent = message;
  statusMessage.className = "status-message";

  if (type) {
    statusMessage.classList.add(type);
  }
}

async function copyToClipboard(text) {
  if (!navigator.clipboard?.writeText) {
    return false;
  }

  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();

  let videoUrl;

  try {
    videoUrl = normaliseYouTubeUrl(videoInput.value);
  } catch (error) {
    showStatus(error.message, "error");
    return;
  }

  const quality = qualityInput.value;

  if (!["720", "1080", "1440", "2160"].includes(quality)) {
    showStatus("Invalid video quality.", "error");
    return;
  }

  // Keep the normalised URL visible for manual copying.
  videoInput.value = videoUrl;

  // Open the workflow from the user's click.
  // GitHub still requires its own Run workflow confirmation.
  const workflowTab = window.open(
    WORKFLOW_URL,
    "_blank",
    "noopener,noreferrer"
  );

  const copied = await copyToClipboard(videoUrl);

  if (copied) {
    showStatus(
      `URL copied. In GitHub Actions, select single mode, ` +
      `paste the URL, choose ${quality}p and run the workflow.`,
      "success"
    );
  } else {
    showStatus(
      `Open GitHub Actions and copy the URL from the box above. ` +
      `Select single mode, choose ${quality}p and run.`,
      "success"
    );
  }

  // Popup blockers can prevent opening a new tab.
  if (!workflowTab) {
    const link = document.createElement("a");
    link.href = WORKFLOW_URL;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open GitHub Actions";
    link.style.color = "#55c5ff";

    statusMessage.append(" ");
    statusMessage.append(link);
  }
});
