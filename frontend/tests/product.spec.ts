import { test, expect, type Page } from "@playwright/test";

async function signIn(page: Page, account: string) {
  await page.goto("/");
  await page.getByLabel("Institution email").fill(`${account === "student" ? "student-"+test.info().project.name : account}@example.invalid`);
  await page.getByLabel("Password", {exact:true}).fill("CampusTest123!");
  await page.getByRole("button", {name:"Sign in",exact:true}).click();
  await expect(page.getByRole("heading",{name:/Good to see you/})).toBeVisible();
}
async function nav(page: Page, name: string) {
  const opener = page.getByRole("button",{name:"Open navigation"});
  if (await opener.isVisible()) await opener.click();
  await page.getByRole("navigation",{name:"Main navigation"}).getByRole("button",{name,exact:true}).click();
}
async function signOut(page: Page) {
  if(await page.getByRole("dialog").count()) await page.keyboard.press("Escape");
  const opener = page.getByRole("button",{name:"Open navigation"});
  if (await opener.isVisible()) await opener.click();
  await page.getByRole("button",{name:"Sign out",exact:true}).click();
}
async function publish(page: Page, filename: string) {
  await signOut(page);
  await signIn(page,"reviewer");
  await nav(page,"Admin Console");
  await page.getByRole("button",{name:"Sources & freshness",exact:true}).click();
  await page.locator(".job-row").filter({hasText:filename}).getByRole("button",{name:"Review publication"}).click();
  const review = page.getByRole("region",{name:"Source publication review"});
  await expect(review).toContainText("Background duplicate check completed");
  await review.getByRole("button",{name:"Approve source",exact:true}).click();
  await expect(review).toContainText("approved");
  await review.getByRole("button",{name:"Publish source",exact:true}).click();
  await expect(review).toContainText("published");
}

test("section administrator publishes a scoped circular without exposing it to section B", async ({page}) => {
  test.setTimeout(120000);
  const stamp = Date.now();
  const filename = `section-${stamp}.txt`, title = `Section product circular ${stamp}`;
  await signIn(page,"section");
  await nav(page,"Admin Console");
  await expect(page.getByLabel("Timetable semester")).toHaveValue("3");
  await expect(page.getByLabel("Timetable section",{exact:true})).toBeDisabled();
  await page.getByLabel("What are you importing?").selectOption("document");
  await expect(page.getByLabel("Source semester (optional)")).toHaveValue("3");
  await expect(page.getByLabel("Source section",{exact:true})).toBeDisabled();
  await expect(page.getByLabel("What are you importing?").getByRole("option",{name:"Canteen menu"})).toHaveCount(0);
  await page.getByLabel("What are you importing?").selectOption("document");
  await page.getByLabel("Document title").fill(title);
  await page.getByLabel("Source file (up to 15 MB)").setInputFiles({name:filename,mimeType:"text/plain",buffer:Buffer.from("Section A product briefing begins at 13:30 on Friday. Reference "+stamp)});
  await page.getByRole("button",{name:"Preview file",exact:true}).click();
  await page.getByRole("button",{name:/Confirm import/}).click();
  await expect(page.getByText(/records processed. Not published./)).toBeVisible({timeout:30000});
  await publish(page,filename);
  await signOut(page);
  await signIn(page,"student");
  await nav(page,"Ask CampusOS");
  await page.getByLabel("Search a document").selectOption({label:title});
  await page.getByLabel("Ask a campus question").fill("When does the product briefing begin?");
  await page.getByRole("button",{name:"Send question",exact:true}).click();
  await expect(page.locator(".chat-answer").last()).toContainText("13:30");
  await signOut(page);
  await signIn(page,"section-b");
  await nav(page,"Ask CampusOS");
  await expect(page.getByLabel("Search a document").getByRole("option",{name:title,exact:true})).toHaveCount(0);
});

test("canteen role manages menus while department role sees official upload workflows", async ({page}) => {
  await signIn(page,"canteen");
  await nav(page,"Admin Console");
  const types = page.getByLabel("What are you importing?");
  await expect(types).toHaveValue("menu");
  await expect(types.locator("option")).toHaveCount(1);
  await expect(page.getByRole("button",{name:"People & roles",exact:true})).toHaveCount(0);
  await signOut(page);
  await signIn(page,"department");
  await nav(page,"Admin Console");
  await expect(types.getByRole("option",{name:"Canteen menu"})).toHaveCount(0);
  await types.selectOption("calendar");
  await expect(page.getByLabel("Department",{exact:true})).toHaveValue("AIML");
  await expect(page.getByLabel("Department",{exact:true})).toBeDisabled();
  await types.selectOption("events");
  await page.getByLabel("What are you importing?").selectOption("exams");
  await expect(page.getByLabel("Event workflow")).toHaveValue("exam");
  await expect(page.getByText("Exam rows may use date, subject, start_time, end_time and venue columns.")).toBeVisible();
});

test("canteen manager edits validated published menu prices and availability through preview", async ({page}) => {
  await signIn(page,"canteen");
  await nav(page,"Admin Console");
  const replacement = page.getByLabel("Replace a published source (optional)");
  await expect.poll(() => replacement.locator("option").count()).toBeGreaterThan(1);
  const id = await replacement.locator("option").nth(1).getAttribute("value");
  await replacement.selectOption(id!);
  await page.getByRole("button",{name:"Load published menu for editing",exact:true}).click();
  await expect(page.getByText(/Selected file: menu-version-/)).toBeVisible();
  await page.getByRole("button",{name:"Preview file",exact:true}).click();
  const price=page.getByLabel("Row 2 price",{exact:true});
  const revisedPrice=String({desktop:42,mobile:43,tablet:44}[test.info().project.name as "desktop"|"mobile"|"tablet"]);
  await price.fill(revisedPrice);
  await page.getByLabel("Row 2 available",{exact:true}).fill("false");
  await expect(page.getByRole("button",{name:/Confirm import/})).toBeDisabled();
  await page.getByRole("button",{name:"Validate changes",exact:true}).click();
  await expect(price).toHaveValue(revisedPrice);
  await expect(page.getByRole("button",{name:/Confirm import/})).toBeEnabled();
  await expect(page.getByLabel("Row 2 available",{exact:true})).toHaveValue("false");
});
