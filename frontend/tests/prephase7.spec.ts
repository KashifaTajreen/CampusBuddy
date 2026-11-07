import {test,expect,type Page} from "@playwright/test";
import path from "node:path";

async function login(page:Page,account:string) {
  await page.goto("/");
  await page.getByLabel("Institution email").fill(`${account}@example.invalid`);
  await page.getByLabel("Password",{exact:true}).fill("CampusTest123!");
  await page.getByRole("button",{name:"Sign in",exact:true}).click();
  await expect(page.getByRole("heading",{name:/Good to see you/})).toBeVisible();
}
async function nav(page:Page,name:string) {
  const opener=page.getByRole("button",{name:"Open navigation"});
  if(await opener.isVisible())await opener.click();
  await page.getByRole("navigation",{name:"Main navigation"}).getByRole("button",{name,exact:true}).click();
}
async function logout(page:Page) {
  if(await page.getByRole("dialog").count())await page.keyboard.press("Escape");
  const opener=page.getByRole("button",{name:"Open navigation"});
  if(await opener.isVisible())await opener.click();
  await page.getByRole("button",{name:"Sign out",exact:true}).click();
}

test("synthetic workbook previews sheets, rejects timetable mapping and supports published own-mentor lookup",async({page})=>{
  test.setTimeout(150000);
  const reference=path.join(process.env.CAMPUS_TEST_DIRECTORY!,"synthetic-project.xlsx");
  await login(page,"admin");
  await nav(page,"Admin Console");
  await page.getByLabel("What are you importing?").selectOption("timetable");
  await page.getByLabel("Timetable file").setInputFiles(reference);
  await page.getByRole("button",{name:"Upload and Extract",exact:true}).click();
  await expect(page.getByText("A timetable grid or scheduling columns could not be read.",{exact:false})).toBeVisible();
  await expect(page.locator('#map-day')).toHaveCount(0);
  await page.getByText("View original file and extraction notes",{exact:true}).click();
  const original=page.getByRole("region",{name:"Original file content preview"});
  await expect(original.getByRole("heading",{name:"Team Details",exact:true})).toBeVisible();
  await expect(original.getByRole("columnheader",{name:"Student 2 USN",exact:true})).toBeVisible();
  await expect(original.getByRole("columnheader",{name:"Faculty Mentor",exact:true})).toBeVisible();
  await expect(page.getByLabel("What are you importing?")).toHaveValue("timetable");
  await page.getByLabel("What are you importing?").selectOption("document");
  await expect(page.getByLabel("What are you importing?")).toHaveValue("document");
  await page.getByLabel("Document title").fill("Skill lab workbook");
  await page.getByLabel("Source file (up to 15 MB)").setInputFiles(reference);
  const response=page.waitForResponse((r)=>r.url().endsWith("/v1/ingestion/preview")&&r.request().method()==="POST");
  await page.getByRole("button",{name:"Preview file",exact:true}).click();
  const job=await(await response).json();
  if(job.source_status!=="published") {
    await page.getByRole("button",{name:/Confirm import/}).click();
    await expect(page.getByText(/records processed. Not published./)).toBeVisible({timeout:45000});
    await logout(page);
    await login(page,"reviewer");
    await nav(page,"Admin Console");
    await page.getByRole("button",{name:"Sources & freshness",exact:true}).click();
    await page.locator(`[data-source-id="${job.source_id}"]`).getByRole("button",{name:"Review publication",exact:true}).click();
    const review=page.getByRole("region",{name:"Source publication review"});
    await review.getByRole("button",{name:"Approve source",exact:true}).click();
    await expect(review).toContainText("approved");
    await review.getByRole("button",{name:"Publish source",exact:true}).click();
    await expect(review).toContainText("published");
  }
  await logout(page);
  await login(page,"student");
  await nav(page,"Ask CampusOS");
  await page.getByRole("button",{name:"New chat",exact:true}).click();
  await page.getByLabel("Ask a campus question").fill("What mentor is allotted to me for skill lab? My USN is 1rv25ci077.");
  await page.getByRole("button",{name:"Send question",exact:true}).click();
  await expect(page.locator(".chat-answer").last()).toContainText("faculty mentor as");
  await expect(page.locator(".citations").last()).toBeVisible();
  await page.getByLabel("Ask a campus question").fill("What mentor is allotted to 1rv25ci088?");
  await page.getByRole("button",{name:"Send question",exact:true}).click();
  await expect(page.locator(".chat-answer").last()).toContainText("only your verified student record");
});

test("long-list deletion dialog stays in viewport, traps focus, cancels and targets the selected source",async({page})=>{
  await login(page,"admin");
  await nav(page,"Admin Console");
  await page.getByRole("button",{name:"Sources & freshness",exact:true}).click();
  const row=page.locator(".job-row").filter({hasText:"Demo draft audit 0.txt"});
  await expect(row).toBeVisible();
  const restore=row.getByRole("button",{name:"Restore as draft",exact:true});
  if(await restore.count()){
    await restore.click();await page.getByRole("button",{name:"Confirm restore",exact:true}).click();
  }
  const trigger=row.getByRole("button",{name:"Delete document",exact:true});
  await trigger.scrollIntoViewIfNeeded();
  const id=await row.getAttribute("data-source-id");
  const before=await trigger.boundingBox();
  await trigger.click();
  const dialog=page.getByRole("dialog",{name:"File lifecycle confirmation"});
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Demo draft audit 0.txt");
  const box=await dialog.boundingBox(),viewport=page.viewportSize()!;
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y+box!.height).toBeLessThanOrEqual(viewport.height+1);
  for(let i=0;i<6;i++){await page.keyboard.press("Tab");expect(await page.evaluate(()=>!!document.activeElement?.closest("dialog"))).toBe(true);}
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(Math.abs((await trigger.boundingBox())!.y-before!.y)).toBeLessThan(3);
  await trigger.click();
  const request=page.waitForRequest((r)=>r.url().endsWith(`/v1/admin/sources/${id}/file`)&&r.method()==="POST");
  await dialog.getByRole("button",{name:"Confirm delete",exact:true}).click();
  await request;
  await expect(row.getByRole("button",{name:"Restore as draft",exact:true})).toBeVisible();
  await expect(row).toContainText("Soft-deleted");
});

test("compound answer retains class and sweet-menu sections and general text preview never executes HTML",async({page})=>{
  await login(page,`student-${test.info().project.name}`);
  await nav(page,"Ask CampusOS");
  await page.getByLabel("Ask a campus question").fill("What class do I have next,also what can I buy from the menu, something sweet under ₹300?");
  await page.getByRole("button",{name:"Send question",exact:true}).click();
  await expect(page.getByRole("region",{name:"Answer part 1"})).toBeVisible();
  await expect(page.getByRole("region",{name:"Answer part 2"})).toContainText("sweet or dessert");
  await logout(page);
  await login(page,"admin");
  await nav(page,"Admin Console");
  await page.getByLabel("What are you importing?").selectOption("document");
  await page.getByLabel("Source file (up to 15 MB)").setInputFiles({name:"safe-preview.txt",mimeType:"text/plain",buffer:Buffer.from('<script>window.previewExecuted=true</script>')});
  await page.getByRole("button",{name:"Preview file",exact:true}).click();
  await expect(page.getByRole("region",{name:"Original file content preview"})).toContainText("<script>");
  expect(await page.evaluate(()=>(window as unknown as {previewExecuted?:boolean}).previewExecuted)).toBeUndefined();
});
