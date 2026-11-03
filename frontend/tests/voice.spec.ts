import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";

/** A short tone, so Chromium's fake microphone has real audio to record. */
function toneWav(seconds = 2, rate = 16000) {
  const samples = seconds * rate;
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + samples * 2, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24);
  buffer.writeUInt32LE(rate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++)
    buffer.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * 440 * i) / rate)), 44 + i * 2);
  return buffer;
}

// PLAYWRIGHT_VOICE_WAV supplies recorded speech for the test that uses the real model.
const realSpeech = process.env.PLAYWRIGHT_VOICE_WAV;
const capture = realSpeech ?? join(mkdtempSync(join(tmpdir(), "campus-voice-")), "tone.wav");
if (!realSpeech) writeFileSync(capture, toneWav());

test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${capture}`,
    ],
  },
});

async function openChat(page: Page) {
  await page.goto("/");
  await page.getByLabel("Institution email").fill("student@example.invalid");
  await page.getByLabel("Password", { exact: true }).fill("CampusTest123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Good to see you/ })).toBeVisible();
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Ask CampusOS", exact: true })
    .click();
  await expect(page.getByLabel("Ask a campus question")).toBeVisible();
}

/** Next.js also renders an empty route announcer with the alert role. */
function errorAlert(page: Page) {
  return page.locator('[role="alert"].error');
}

function stubTranscript(page: Page, status: number, body: object) {
  return page.route("**/v1/speech/transcribe", (route) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }),
  );
}

async function record(page: Page) {
  await page.getByRole("button", { name: "Start voice input" }).click();
  const stop = page.getByRole("button", { name: "Stop voice input" });
  await expect(stop).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("0:01")).toBeVisible();
  await stop.click();
}

test("the page allows its own microphone use", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.headers()["permissions-policy"]).toContain("microphone=(self)");
});

test("a recording is uploaded, and the transcript waits in the box without sending", async ({
  page,
}) => {
  await stubTranscript(page, 200, { text: "What is my next class?", language: "en", duration: 2 });
  const uploads: { type: string; size: number }[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/v1/speech/transcribe"))
      uploads.push({
        type: request.headers()["content-type"] ?? "",
        size: request.postDataBuffer()?.length ?? 0,
      });
  });
  const chats: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/v1/chat")) chats.push(request.url());
  });
  await openChat(page);
  await record(page);
  const field = page.getByLabel("Ask a campus question");
  await expect(field).toHaveValue("What is my next class?");
  await expect(field).toBeFocused();
  expect(uploads).toHaveLength(1);
  expect(uploads[0].type).toContain("multipart/form-data");
  expect(uploads[0].size).toBeGreaterThan(1000);
  expect(chats).toEqual([]);
  await expect(page.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Send question" })).toBeEnabled();
});

test("speech is added after text that was already typed", async ({ page }) => {
  await stubTranscript(page, 200, { text: "on Friday", language: "en", duration: 1 });
  await openChat(page);
  await page.getByLabel("Ask a campus question").fill("Show my timetable");
  await record(page);
  await expect(page.getByLabel("Ask a campus question")).toHaveValue("Show my timetable on Friday");
});

test("sending is blocked while recording", async ({ page }) => {
  await stubTranscript(page, 200, { text: "Hello", language: "en", duration: 1 });
  await openChat(page);
  await page.getByLabel("Ask a campus question").fill("Typed question");
  await page.getByRole("button", { name: "Start voice input" }).click();
  await expect(page.getByRole("button", { name: "Send question" })).toBeDisabled();
  await page.getByRole("button", { name: "Stop voice input" }).click();
  await expect(page.getByRole("button", { name: "Send question" })).toBeEnabled();
});

test("a server without a speech model shows the setup message and recovers", async ({ page }) => {
  await stubTranscript(page, 503, {
    detail: "Voice input is not set up on this server. Type your question instead.",
  });
  await openChat(page);
  await record(page);
  await expect(errorAlert(page)).toContainText("Voice input is not set up");
  await expect(page.getByRole("button", { name: "Start voice input" })).toBeEnabled();
  const field = page.getByLabel("Ask a campus question");
  await expect(field).toBeEnabled();
  await expect(field).toHaveValue("");
});

test("silence asks the user to try again", async ({ page }) => {
  await stubTranscript(page, 200, { text: "", language: "en", duration: 2 });
  await openChat(page);
  await record(page);
  await expect(errorAlert(page)).toContainText("didn't catch that");
});

test("a denied microphone explains how to recover", async ({ page }) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () =>
      Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
  });
  await openChat(page);
  await page.getByRole("button", { name: "Start voice input" }).click();
  await expect(errorAlert(page)).toContainText("Microphone access is blocked");
  await expect(page.getByRole("button", { name: "Start voice input" })).toBeEnabled();
});

test("browsers that cannot record do not show the button", async ({ page }) => {
  await page.addInitScript(() => {
    Reflect.deleteProperty(window, "MediaRecorder");
  });
  await openChat(page);
  await expect(page.getByRole("button", { name: "Start voice input" })).toHaveCount(0);
});

test("recorded speech is transcribed by the real model", async ({ page }) => {
  test.skip(!realSpeech, "Set PLAYWRIGHT_VOICE_WAV to a spoken WAV and install the speech model.");
  test.setTimeout(120000);
  await openChat(page);
  await page.getByRole("button", { name: "Start voice input" }).click();
  await expect(page.getByText("0:04")).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: "Stop voice input" }).click();
  await expect(page.getByLabel("Ask a campus question")).toHaveValue(/class/i, { timeout: 90000 });
});
