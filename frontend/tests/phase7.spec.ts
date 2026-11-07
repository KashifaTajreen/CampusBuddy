import { test, expect } from "@playwright/test";

test("Phase 7 workspace selection never grants the selected role", async ({page}) => {
  await page.goto("/");
  await expect(page.getByRole("radio")).toHaveCount(5);
  for(const role of ["College Admin","Department Admin","Section Admin","Canteen Manager","Student"])
    await expect(page.getByRole("radio",{name:role,exact:true})).toBeVisible();
  await page.getByRole("radio",{name:"College Admin",exact:true}).check();
  await page.getByLabel("Institution email").fill("student@example.invalid");
  await page.getByLabel("Password",{exact:true}).fill("CampusTest123!");
  await page.getByRole("button",{name:"Sign in",exact:true}).click();
  await expect(page.getByRole("heading",{name:/Good to see you/})).toBeVisible();
  const opener=page.getByRole("button",{name:"Open navigation"});
  if(await opener.isVisible()) await opener.click();
  await expect(page.locator(".sidebar-person")).toContainText("Student");
  await expect(page.getByRole("button",{name:"Admin Console",exact:true})).toHaveCount(0);
  const status=await page.evaluate(async (origin)=>{
    const raw=Object.keys(localStorage).find(k=>k.startsWith("sb-")&&k.endsWith("-auth-token"));
    const session=JSON.parse(localStorage.getItem(raw!)!);
    return (await fetch(origin+"/v1/admin/memberships",{headers:{Authorization:"Bearer "+session.access_token,
      "X-Tenant-ID":"00000000-0000-4000-8000-000000000001"}})).status;
  },`http://127.0.0.1:${process.env.CAMPUS_TEST_API_PORT||8765}`);
  expect(status).toBe(403);
});

test("Phase 7 PWA has install icons and public-only offline fallback", async ({page,context}) => {
  await page.goto("/");
  const response=await page.request.get("/manifest.webmanifest");
  expect(response.ok()).toBe(true);
  const manifest=await response.json();
  expect(manifest.display).toBe("standalone");
  expect(manifest.icons.some((i:{purpose:string})=>i.purpose==="maskable")).toBe(true);
  for(const icon of manifest.icons) expect((await page.request.get(icon.src)).ok()).toBe(true);
  const worker=await page.request.get("/sw.js");
  expect(worker.headers()["cache-control"]).toContain("no-store");
  await page.evaluate(()=>navigator.serviceWorker.ready);
  await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
  const cached=await page.evaluate(async()=>{
    const entries=[];
    for(const name of await caches.keys()) for(const request of await (await caches.open(name)).keys()) entries.push(new URL(request.url).pathname);
    return entries;
  });
  expect(cached.sort()).toEqual(["/icons/icon-192.png","/icons/icon-512.png","/icons/maskable-512.png","/offline.html"].sort());
  await context.setOffline(true);
  await page.goto("/");
  await expect(page.getByRole("heading",{name:"You’re offline"})).toBeVisible();
  await expect(page.getByText(/Private campus information and answers are not cached/)).toBeVisible();
  await context.setOffline(false);
  await page.getByRole("link",{name:"Try CampusOS again"}).click();
  await expect(page.getByLabel("Institution email")).toBeVisible();
});
