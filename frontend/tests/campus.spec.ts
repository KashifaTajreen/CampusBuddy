import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function signIn(page: Page, admin: boolean | "reviewer" = false) {
  await page.goto("/");
  await page
    .getByLabel("Institution email")
    .fill(
      admin === "reviewer"
        ? "reviewer@example.invalid"
        : admin
          ? "admin@example.invalid"
          : `student-${test.info().project.name}@example.invalid`,
    );
  await page.getByLabel("Password", { exact: true }).fill("CampusTest123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /Good to see you/ }),
  ).toBeVisible();
}
async function nav(page: Page, name: string) {
  await expect(page.getByRole("combobox",{name:"Institution",exact:true})).toBeVisible();
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name, exact: true })
    .click();
}
async function signOut(page: Page) {
  if(await page.getByRole("dialog").count()) await page.keyboard.press("Escape");
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
}

test("admin file deletion, draft restoration and explicit permanent confirmation", async ({ page }) => {
  await signIn(page, true);
  await nav(page, "Admin Console");
  const filename = `lifecycle-${Date.now()}.txt`;
  await page.getByLabel("What are you importing?").selectOption("document");
  await page.getByLabel("Source file (up to 15 MB)").setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(`A test-only campus notice for the file lifecycle demonstration. ${filename}`) });
  await page.getByRole("button", { name: "Preview file", exact: true }).click();
  await expect(page.getByRole("button", {name:/Confirm import/})).toBeEnabled();
  await page.getByRole("button", { name: "Sources & freshness", exact: true }).click();
  let row = page.locator(".job-row").filter({ hasText: filename });
  await row.getByRole("button", { name: "Delete document", exact: true }).click();
  await page.getByRole("button", { name: "Confirm delete", exact: true }).click();
  await expect(row.getByRole("button", { name: "Restore as draft" })).toBeVisible();
  await row.getByRole("button", { name: "Restore as draft" }).click();
  await page.getByRole("button", { name: "Confirm restore", exact: true }).click();
  await expect(row.getByRole("button", { name: "Delete document" })).toBeVisible();
  await row.getByRole("button", { name: "Delete document" }).click();
  await page.getByRole("button", { name: "Confirm delete", exact: true }).click();
  await row.getByRole("button", { name: "Permanently delete" }).click();
  await expect(page.getByRole("button", { name: "Confirm purge", exact: true })).toBeDisabled();
  const label = await page.locator('label[for="purge-confirmation"]').innerText();
  await page.getByLabel(/^Type DELETE /).fill(label.replace("Type ", ""));
  await page.getByRole("button", { name: "Confirm purge", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "File lifecycle confirmation" })).toHaveCount(0);
  await expect(page.locator(".job-row").filter({ hasText: filename })).toHaveCount(0);
});

test("language selection, multilingual routing and multi-part results", async ({ page }) => {
  await signIn(page);
  await nav(page, "Ask CampusOS");
  await page.getByLabel("Response language").selectOption("hi");
  await page.getByLabel("Ask a campus question").fill("Who teaches Machine Learning and where is their office?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.getByRole("region", { name: "Answer part 1" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Answer part 2" })).toBeVisible();
  await expect(page.getByLabel("Response language")).toHaveValue("hi");
  await page.reload();
  await nav(page, "Ask CampusOS");
  await page.getByLabel("Your conversations").selectOption({ label: "Who teaches Machine Learning and where is their office?" });
  await expect(page.getByLabel("Response language")).toHaveValue("hi");
  await page.getByLabel("Response language").selectOption("auto");
  await page.getByLabel("Ask a campus question").fill("ಇಂದು ನನ್ನ ತರಗತಿಗಳು");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".chat-answer").last()).toContainText("ನಿಮ್ಮ ತರಗತಿಗಳು");
});

test("selected document follow-ups, restore, switching, citations and clearing", async ({
  page,
}) => {
  await signIn(page);
  await nav(page, "Ask CampusOS");
  const selector = page.getByLabel("Search a document");
  await expect(
    selector.getByRole("option", {
      name: "Selection Handbook Alpha",
      exact: true,
    }),
  ).toHaveCount(1);
  await selector.selectOption({ label: "Selection Handbook Alpha" });
  await expect(
    page.getByText("Searching only: Selection Handbook Alpha", { exact: true }),
  ).toBeVisible();
  const ask = async (question: string) => {
    const previousAnswers = await page.locator(".chat-answer").count();
    await page.getByLabel("Ask a campus question").fill(question);
    await page
      .getByRole("button", { name: "Send question", exact: true })
      .click();
    await expect(page.locator(".chat-answer")).toHaveCount(previousAnswers + 1);
    await expect(
      page.locator(".chat-message").last().locator(".assistant-label"),
    ).toContainText("CampusOS");
  };
  await ask("Zirconium scholarship deadline");
  await expect(page.locator(".chat-answer").last()).toContainText("Friday");
  await expect(page.locator(".citations").last()).toContainText(
    /Selection Handbook Alpha.*p\..*2/,
  );
  await ask("Tell me more about that");
  await expect(page.locator(".chat-answer").last()).toContainText("Friday");
  const conversationId = await page.locator("#conversation").inputValue();
  await page.reload();
  await nav(page, "Ask CampusOS");
  await page.getByLabel("Your conversations").selectOption(conversationId);
  await expect(selector).toHaveValue(/.+/);
  await expect(
    page.getByText("Searching only: Selection Handbook Alpha", { exact: true }),
  ).toBeVisible();
  await selector.selectOption({ label: "Selection Handbook Beta" });
  await ask("deadline");
  await expect(page.locator(".chat-answer").last()).toContainText("Tuesday");
  await expect(page.locator(".chat-answer").last()).not.toContainText("Friday");
  await ask("xylophonicquasarunrecorded");
  await expect(page.locator(".chat-answer").last()).toContainText(
    "Insufficient evidence",
  );
  await page.getByRole("button", { name: "Clear document selection" }).click();
  await expect(selector).toHaveValue("");
  await page.getByLabel("Your conversations").selectOption("");
  await page.getByLabel("Your conversations").selectOption(conversationId);
  await expect(page.getByLabel("Your conversations")).toHaveValue(
    conversationId,
  );
  await expect(selector).toHaveValue("");
  await ask("My next class");
  await expect(page.locator(".chat-answer").last()).toContainText(
    "Your next class",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("document list failure, empty state and unavailable selection recover", async ({
  page,
}) => {
  let empty = false;
  await page.route("**/v1/documents?*", (route) =>
    route.fulfill({
      status: empty ? 200 : 503,
      contentType: "application/json",
      body: JSON.stringify(
        empty
          ? { documents: [], next_offset: null }
          : { detail: "Document list temporarily unavailable" },
      ),
    }),
  );
  await signIn(page);
  await nav(page, "Ask CampusOS");
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Document list temporarily unavailable" }),
  ).toContainText("Document list temporarily unavailable");
  empty = true;
  await page.getByRole("button", { name: "Retry document list" }).click();
  await expect(
    page.getByText(
      "No published documents available. You can still ask general campus questions.",
    ),
  ).toBeVisible();
  await page.unroute("**/v1/documents?*");
  await page.reload();
  await nav(page, "Ask CampusOS");
  await page
    .getByLabel("Search a document")
    .selectOption({ label: "Selection Handbook Alpha" });
  await page.route("**/v1/chat/stream", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "The selected document is unavailable." }),
    }),
  );
  await page.getByLabel("Ask a campus question").fill("scholarship");
  await page
    .getByRole("button", { name: "Send question", exact: true })
    .click();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "The selected document is unavailable." }),
  ).toContainText("The selected document is unavailable.");
  await expect(page.getByLabel("Ask a campus question")).toBeDisabled();
  await page.getByRole("button", { name: "Clear document selection" }).click();
  await expect(page.getByLabel("Ask a campus question")).toBeEnabled();
});

test("home, next class, contextual follow-up and cited source", async ({
  page,
}, info) => {
  await signIn(page);
  await expect(
    page.getByPlaceholder("Ask anything about your campus..."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Admin Console", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".next-panel")).toBeVisible();
  await page.screenshot({
    path: `../artifacts/screenshots/home-${info.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "My next class", exact: true })
    .click();
  await expect(page.locator(".chat-answer").first()).toBeVisible();
  await expect(page.locator(".citations button").first()).toBeVisible();
  await page
    .getByLabel("Ask a campus question")
    .fill("Where is that professor’s office?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".chat-message").last()).toContainText("Block A");
});
test("vegetarian budget and meal calculations", async ({ page }) => {
  await signIn(page);
  await nav(page, "Canteen");
  await page.getByLabel("Maximum budget").fill("100");
  await page.getByLabel("Dietary preference").selectOption("true");
  await expect(page.locator(".food-grid .record-card").first()).toBeVisible();
  await expect(page.locator(".nonveg")).toHaveCount(0);
  const prices = await page.locator(".food-grid .price").allTextContents();
  expect(prices.every((p) => Number(p.replace("₹", "")) <= 100)).toBeTruthy();
  await page
    .getByRole("button", { name: "Meal combinations", exact: true })
    .click();
  await expect(
    page.getByText(/estimated protein · Vegetarian/).first(),
  ).toBeVisible();
});
test("navigation, missing information and narrow screen layout", async ({
  page,
}) => {
  await signIn(page);
  await nav(page, "Timetable");
  await page.getByLabel("Date", { exact: true }).fill("2030-01-01");
  await expect(page.getByText("No records yet.")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await nav(page, "Notices");
  await expect(
    page.getByRole("heading", { name: "Notices", exact: true }),
  ).toBeVisible();
});
test("processing, independent approval and publication", async ({ page }) => {
  test.setTimeout(120000);
  const stamp = Date.now();
  const filename = `qa-menu-${stamp}.csv`;
  const item = `QA dal ${stamp}`;
  await signIn(page, true);
  await nav(page, "Admin Console");
  await page.getByLabel("What are you importing?").selectOption("menu");
  await page.getByLabel("Source file").setInputFiles({
    name: filename,
    mimeType: "text/csv",
    buffer: Buffer.from(
      `name,price,vegetarian,category,location\n${item},55,true,Meal,QA Canteen\n`,
    ),
  });
  await page.getByRole("button", { name: "Preview file" }).click();
  await expect(
    page.getByRole("heading", { name: "Review your import" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Confirm import/ }).click();
  await expect(page.getByText(/records processed. Not published./)).toBeVisible(
    { timeout: 30000 },
  );
  await signOut(page);
  await signIn(page);
  await nav(page, "Canteen");
  await page.getByLabel("Search canteen").fill(item);
  await expect(page.getByText("No records yet.")).toBeVisible();
  await signOut(page);
  await signIn(page, "reviewer");
  await nav(page, "Admin Console");
  await page
    .getByRole("button", { name: "Sources & freshness", exact: true })
    .click();
  await page
    .locator(".job-row")
    .filter({ hasText: filename })
    .getByRole("button", { name: "Review publication" })
    .click();
  const review = page.getByRole("region", {
    name: "Source publication review",
  });
  await review
    .getByRole("button", { name: "Approve source", exact: true })
    .click();
  await expect(review).toContainText("approved");
  await review
    .getByRole("button", { name: "Publish source", exact: true })
    .click();
  await expect(review).toContainText("published");
  await signOut(page);
  await signIn(page);
  await nav(page, "Canteen");
  await page.getByLabel("Search canteen").fill(item);
  await expect(
    page.getByRole("heading", { name: item, exact: true }),
  ).toBeVisible();
});
test("upload validation provides recovery", async ({ page }) => {
  await signIn(page, true);
  await nav(page, "Admin Console");
  await page.getByLabel("What are you importing?").selectOption("menu");
  await page.getByLabel("Source file").setInputFiles({
    name: "invalid-menu.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "name,price,vegetarian,category,location\nInvalid,-5,true,Meal,QA\n",
    ),
  });
  await page.getByRole("button", { name: "Preview file" }).click();
  await expect(page.getByText(/record\(s\) need attention/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Confirm import/ }),
  ).toBeDisabled();
});
test("accessibility and API failure recovery", async ({ page }) => {
  await signIn(page);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
  await page.route("**/v1/menu?**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Menu temporarily unavailable. Try again.",
      }),
    }),
  );
  await nav(page, "Canteen");
  await expect(
    page.getByRole("alert").filter({ hasText: "Menu temporarily unavailable" }),
  ).toContainText("Menu temporarily unavailable");
  await expect(
    page.getByRole("button", { name: "Refresh", exact: true }),
  ).toBeVisible();
});
