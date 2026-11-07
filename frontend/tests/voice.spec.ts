import { test, expect, type Page } from "@playwright/test";
import path from "node:path";

// Synthetic audio only. Chrome never opens the operator's physical microphone.
test.use({ permissions: ["microphone"], launchOptions: { args: [
  "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
  `--use-file-for-fake-audio-capture=${path.join(process.env.CAMPUS_TEST_REPOSITORY_ROOT || path.resolve(".."),"backend/tests/fixtures/speech-question.wav")}`,
] } });

async function openChat(page: Page) {
  await page.goto("/");
  await page.getByLabel("Institution email").fill(`student-${test.info().project.name}@example.invalid`);
  await page.getByLabel("Password", { exact: true }).fill("CampusTest123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Good to see you/ })).toBeVisible();
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Ask CampusOS", exact: true }).click();
}

test("real MediaRecorder audio reaches local Whisper and authenticated chat", async ({ page }) => {
  // Keep Chrome DSP from altering an already-clean synthetic capture fixture.
  // This does not change production microphone settings or prove human quality.
  await page.addInitScript(()=>{
    const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia=(constraints)=>capture({...constraints,audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
  });
  await openChat(page);
  await page.getByLabel("Response language").selectOption("en");
  await expect(page.getByLabel("Response language")).toHaveValue("en");
  const policy = (await page.request.get("/")).headers()["permissions-policy"];
  expect(policy).toContain("microphone=(self)");
  const capabilities = page.waitForResponse((r) => r.url().includes("/v1/voice/capabilities"));
  await page.getByRole("button", { name: "Start microphone", exact: true }).click();
  const ready = await (await capabilities).json();
  test.skip(!ready.local_stt_ready, "Approved optional Whisper-tiny assets or dependencies are not installed.");
  await expect(page.getByRole("button", { name: "Stop recording", exact: true })).toBeVisible();
  await expect(page.getByLabel("Ask a campus question")).toBeDisabled();
  await page.waitForTimeout(4600); // Include a full 2.17-second fixture cycle even if capture begins mid-loop.
  await page.getByRole("button", { name: "Stop recording", exact: true }).click();
  await expect(page.getByLabel("Ask a campus question")).toHaveValue(/next class/i, { timeout: 45000 });
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".chat-answer").last()).toBeVisible();
  // Voice-origin questions may auto-read the answer immediately; the same
  // control is then correctly labeled Stop spoken answer.
  await expect(page.getByRole("button", { name: /^(Read answer aloud|Stop spoken answer)$/ }).last()).toBeVisible();
  await expect(page.locator(".citations").last()).toBeVisible();
});

test("new conversation cancels a pending permission request and discards late audio", async ({ page }) => {
  await page.addInitScript(() => {
    const state = { stopped: 0, resolve: (_stream: unknown) => {} };
    Object.assign(window, { captureTest: state });
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { value: () => new Promise((resolve) => { state.resolve = resolve; }) });
  });
  await page.route("**/v1/voice/capabilities", (route) => route.fulfill({ json: { local_stt_ready: true } }));
  await openChat(page);
  await page.getByRole("button", { name: "Start microphone", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel recording", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page.evaluate(() => {
    const state = (window as unknown as { captureTest: { stopped: number; resolve: (value: unknown) => void } }).captureTest;
    state.resolve({ getTracks: () => [{ stop: () => { state.stopped++; } }] });
  });
  await expect.poll(() => page.evaluate(() => (window as unknown as { captureTest: { stopped: number } }).captureTest.stopped)).toBe(1);
  await expect(page.getByRole("button", { name: "Stop recording", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Ask a campus question")).toBeEnabled();
  await expect(page.getByLabel("Ask a campus question")).toHaveValue("");
});

test("Auto recording returns a supported-language transcript or an actionable quality retry",async({page},info)=>{
  await openChat(page);
  await page.getByLabel("Response language").selectOption("auto");
  const capabilities=page.waitForResponse(r=>r.url().includes('/v1/voice/capabilities'));
  await page.getByRole("button",{name:"Start microphone",exact:true}).click();
  const ready=await(await capabilities).json();
  test.skip(!ready.local_stt_ready,"Approved optional local Whisper assets or dependencies are not installed.");
  await expect(page.getByRole("button",{name:"Stop recording",exact:true})).toBeVisible();
  await page.waitForTimeout(4600);
  const response=page.waitForResponse((r)=>r.url().includes("/v1/voice/transcribe"),{timeout:45000});
  await page.getByRole("button",{name:"Stop recording",exact:true}).click();
  const result=await response;
  const value=await result.json();
  await info.attach("constrained-auto-speech",{body:JSON.stringify({synthetic_audio:true,status:result.status(),language:value.language,strategy:value.language_strategy,retry_reason:value.detail}),contentType:"application/json"});
  if(result.ok()){
    expect(["en","hi","kn"]).toContain(value.language);
    expect(value.language_strategy).toBe("three_forced_decodes");
    await expect(page.getByLabel("Ask a campus question")).not.toHaveValue("");
  }else{
    expect(result.status()).toBe(400);
    await expect(page.locator(".voice-controls").getByRole("alert")).toContainText(/ambiguous|unclear|clear speech/);
    await expect(page.getByRole("button",{name:"Retry microphone",exact:true})).toBeEnabled();
  }
  await expect(page.getByLabel("Ask a campus question")).toBeEnabled();
});

test("real device TTS availability has a truthful text fallback", async ({ page }, info) => {
  await openChat(page);
  await page.getByLabel("Ask a campus question").fill("What is my next class?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  const voices = await page.evaluate(async () => {
    if (!("speechSynthesis" in window)) return [];
    if (!speechSynthesis.getVoices().length) await new Promise((resolve) => { speechSynthesis.addEventListener("voiceschanged", resolve, { once: true }); setTimeout(resolve, 2000); });
    return speechSynthesis.getVoices().map((v) => ({ name: v.name, language: v.lang, local: v.localService }));
  });
  await info.attach("actual-device-voices", { body: JSON.stringify(voices), contentType: "application/json" });
  await page.getByRole("button", { name: "Read answer aloud", exact: true }).last().click();
  if (!voices.some((voice) => voice.local && voice.language.startsWith("en"))) {
    await expect(page.locator(".spoken-answer").getByRole("status")).toContainText(/No local device voice|playback is unavailable/);
  } else {
    await expect(page.getByRole("button", { name: "Stop spoken answer", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Stop spoken answer", exact: true }).click();
  }
  await expect(page.locator(".chat-answer").last()).toBeVisible();
});

test("microphone denial recovers to text", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { value: async () => { throw new DOMException("Denied for test", "NotAllowedError"); } });
  });
  await page.route("**/v1/voice/capabilities", (route) => route.fulfill({ json: { local_stt_ready: true } }));
  await openChat(page);
  await page.getByRole("button", { name: "Start microphone", exact: true }).click();
  await expect(page.locator(".voice-controls").getByRole("alert")).toContainText("Microphone permission denied");
  await expect(page.getByLabel("Ask a campus question")).toBeEnabled();
  await page.getByLabel("Ask a campus question").fill("What is my next class?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".chat-answer").last()).toBeVisible();
});

test("local TTS contract supports stop and replay without a remote voice", async ({ page }) => {
  // Mocked output contract: tests control flow, not naturalness or audible quality.
  await page.addInitScript(() => {
    const state = { spoken: 0, cancelled: 0, local: false };
    Object.assign(window, { voiceTest: state });
    Object.defineProperty(window, "SpeechSynthesisUtterance", { value: class {
      constructor(public text: string) {}
      voice: { localService: boolean } | null = null;
    } });
    Object.defineProperty(window, "speechSynthesis", { value: {
      getVoices: () => [{ localService: false, lang: "en-IN" }, { localService: true, lang: "en-US" }],
      speak: (value: SpeechSynthesisUtterance) => { state.spoken++; state.local = value.voice?.localService === true; },
      cancel: () => { state.cancelled++; },
    } });
  });
  await openChat(page);
  await page.getByLabel("Ask a campus question").fill("What is my next class?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await page.getByRole("button", { name: "Read answer aloud", exact: true }).last().click();
  await expect(page.getByRole("button", { name: "Stop spoken answer", exact: true })).toBeVisible();
  const first = await page.evaluate(() => (window as unknown as {voiceTest:{spoken:number}}).voiceTest.spoken);
  expect(first).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Stop spoken answer", exact: true }).click();
  await page.getByRole("button", { name: "Read answer aloud", exact: true }).last().click();
  const state = await page.evaluate(() => (window as unknown as { voiceTest: { spoken: number; cancelled: number; local: boolean } }).voiceTest);
  // Long answers are intentionally split into short utterances; replay every
  // segment, rather than assuming one utterance per click.
  expect(state.spoken).toBe(first * 2);
  expect(state.cancelled).toBeGreaterThan(0);
  expect(state.local).toBe(true);
});
