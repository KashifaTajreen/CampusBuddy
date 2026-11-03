import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function signIn(page: Page, admin = false) {
  await page.goto("/");
  await page
    .getByLabel("Institution email")
    .fill(admin ? "admin@example.invalid" : "student@example.invalid");
  await page.getByLabel("Password", { exact: true }).fill("CampusTest123!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /Good to see you/ }),
  ).toBeVisible();
}
async function nav(page: Page, name: string) {
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name, exact: true })
    .click();
}
async function openEditor(page: Page) {
  await signIn(page, true);
  await nav(page, "Admin Console");
  // The sidebar also has a Timetable button, so this targets the Admin tab.
  await page
    .locator(".tabs")
    .getByRole("button", { name: "Timetable", exact: true })
    .click();
  await expect(page.getByRole("table")).toBeVisible();
}
async function fillClass(
  page: Page,
  subject: string,
  day: string,
  start: string,
  end: string,
  room: string,
) {
  await page.getByLabel("Subject").fill(subject);
  await expect(
    page.getByLabel("Teacher").locator("option").nth(1),
  ).toBeAttached();
  await page.getByLabel("Teacher").selectOption({ index: 1 });
  await page.getByLabel("Room").fill(room);
  await page.getByLabel("Day", { exact: true }).selectOption(day);
  await page.getByLabel("Start time").fill(start);
  await page.getByLabel("End time").fill(end);
}
function submitNewClass(page: Page) {
  return page
    .getByRole("form", { name: "Add class" })
    .getByRole("button", { name: "Add class" })
    .click();
}

test("student week view shows a grid with initials, rooms and a legend", async ({
  page,
}) => {
  await signIn(page);
  await nav(page, "Timetable");
  await page.getByRole("button", { name: "Week view" }).click();
  const table = page.getByRole("table");
  await expect(
    table.getByRole("columnheader", { name: "Monday" }),
  ).toBeVisible();
  await expect(
    table.getByRole("rowheader", { name: "09:00 to 10:00" }),
  ).toBeVisible();
  // Students read the timetable; only administrators get edit buttons.
  await expect(table.getByRole("button")).toHaveCount(0);
  await expect(page.locator(".timetable-demo")).toContainText("Demo Data");
  await expect(page.getByRole("heading", { name: "Teachers" })).toBeVisible();
  await expect(page.locator(".timetable-legend dd").first()).toContainText(
    "Dr.",
  );
  const results = await new AxeBuilder({ page })
    .include(".timetable-grid")
    .analyze();
  expect(results.violations).toEqual([]);
});

test("administrator adds, edits and deletes a class", async ({ page }) => {
  await openEditor(page);
  // The demo timetable runs Monday to Friday, so a Saturday class cannot clash.
  await page.getByRole("button", { name: "Add class" }).click();
  await fillClass(page, "Guest Lecture", "5", "09:00", "10:00", "Z-900");
  await submitNewClass(page);
  await expect(page.getByText("Added Guest Lecture.")).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "Saturday" }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: /^Edit Guest Lecture, Saturday/ })
    .click();
  await page.getByLabel("Subject").fill("Guest Seminar");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Saved Guest Seminar.")).toBeVisible();

  // Deleting restores the seed data for the next browser project in this run.
  await page
    .getByRole("button", { name: /^Edit Guest Seminar, Saturday/ })
    .click();
  await page.getByRole("button", { name: "Delete class" }).click();
  await page.getByRole("button", { name: "Yes, delete class" }).click();
  await expect(page.getByText("Deleted Guest Seminar.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Edit Guest Seminar/ }),
  ).toHaveCount(0);
});

test("a clashing class shows the reason and stays in the form", async ({
  page,
}) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add class" }).click();
  // Every seeded section has a class on Monday at 09:00.
  await fillClass(page, "Clashing Class", "0", "09:00", "10:00", "Z-901");
  await submitNewClass(page);
  await expect(
    page.getByRole("form", { name: "Add class" }).getByRole("alert"),
  ).toContainText("already has");
  await expect(page.getByLabel("Subject")).toHaveValue("Clashing Class");
});

test("the form explains missing details before sending anything", async ({
  page,
}) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add class" }).click();
  await submitNewClass(page);
  await expect(page.getByText("Enter a subject.")).toBeVisible();
  await expect(page.getByText("Choose a teacher.")).toBeVisible();
});

test("faculty cards show profile details and an expandable biography", async ({
  page,
}) => {
  await signIn(page);
  const opener = page.getByRole("button", { name: "Open navigation" });
  if (await opener.isVisible()) await opener.click();
  const menu = page.getByRole("navigation", { name: "Main navigation" });
  await menu.getByRole("button", { name: "More" }).click();
  await menu.getByRole("button", { name: "Faculty", exact: true }).click();
  await expect(page.getByText("Qualification").first()).toBeVisible();
  const toggle = page
    .getByRole("button", { name: /^Show biography of/ })
    .first();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(
    page.getByRole("button", { name: /^Hide biography of/ }).first(),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/Synthetic profile/).first()).toBeVisible();
});
