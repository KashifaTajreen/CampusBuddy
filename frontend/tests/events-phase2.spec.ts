import {test,expect,type Page} from '@playwright/test';
import path from 'node:path';

async function signIn(page:Page,admin:boolean){
 await page.goto('/');
 await page.getByLabel('Institution email').fill(admin?'admin@example.invalid':'student@example.invalid');
 await page.getByLabel('Password',{exact:true}).fill('CampusTest123!');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.getByRole('heading',{name:/Good to see you/})).toBeVisible();
}
async function nav(page:Page,name:string){
 const opener=page.getByRole('button',{name:'Open navigation'});
 if(await opener.isVisible())await opener.click();
 const navigation=page.getByRole('navigation',{name:'Main navigation'});
 let button=navigation.getByRole('button',{name,exact:true});
 if(!await button.isVisible()){
  await navigation.getByRole('button',{name:'More',exact:true}).click();
  button=navigation.getByRole('button',{name,exact:true});
 }
 await button.click();
}
async function signOut(page:Page){
 const opener=page.getByRole('button',{name:'Open navigation'});
 if(await opener.isVisible())await opener.click();
 await page.getByRole('button',{name:'Sign out',exact:true}).click();
 await expect(page.getByLabel('Institution email')).toBeVisible();
}

test('synthetic poster OCR → edit → E5 → same-admin publish → student Events and cited RAG',async({page})=>{
 test.setTimeout(160000);
 // No request from this verification may reach the live API or Supabase.
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(['http:','https:'].includes(url.protocol)&&(!['127.0.0.1','localhost'].includes(url.hostname)||!['3102','8865','8866'].includes(url.port))){
   throw new Error('Blocked non-disposable network target in Events test');
  }
  await route.continue();
 });
 await signIn(page,true);
 await nav(page,'Admin Console');
 await page.getByLabel('What are you importing?').selectOption('events');
 await expect(page.getByText('Recommended Event Information',{exact:true})).toBeVisible();
 await page.getByLabel('Event poster or document').setInputFiles(path.join(process.env.CAMPUS_TEST_EVENT_FIXTURES!,'poster.png'));
 await page.getByRole('button',{name:'Upload Event',exact:true}).click();
 const preview=page.getByRole('region',{name:'Event preview',exact:true});
 await expect(preview.getByLabel('Title',{exact:true})).toHaveValue(/Lantern/i,{timeout:60000});
 await expect(preview).not.toContainText('not a matching structured table');
 await preview.getByText('Location and attendance (optional)',{exact:true}).click();
 await preview.getByLabel('Location',{exact:true}).fill('Reviewed Test Hall');
 await preview.getByText('Full extracted text — review or correct',{exact:true}).click();
 await expect(preview.getByLabel('Reviewed text · Page 1')).toHaveValue(/original artwork/i);
 await preview.getByRole('checkbox',{name:/I reviewed the OCR text/}).check();
 await preview.getByRole('button',{name:'Validate Event',exact:true}).click();
 await expect(preview).toContainText('1 events ready');
 const publish=page.getByRole('region',{name:'Publish Event',exact:true});
 await expect(publish.getByRole('button',{name:'Publish Event',exact:true})).toBeEnabled();
 await publish.getByRole('button',{name:'Publish Event',exact:true}).click();
 await expect(publish).toContainText('Published 1 event(s)',{timeout:45000});
 await expect(preview).toContainText('E5 passages indexed');
 // A second event deliberately has no date, fee or location.
 await page.getByLabel('Event poster or document').setInputFiles({name:'undated-club.txt',mimeType:'text/plain',buffer:Buffer.from('Title: Moonlight Club Recruitment\nCategory: Club activities\nDescription: A synthetic student club recruitment drive.\nRules\nBring a sketchbook for the portfolio discussion.')});
 await page.getByRole('button',{name:'Upload Event',exact:true}).click();
 await expect(preview.getByLabel('Title',{exact:true})).toHaveValue('Moonlight Club Recruitment');
 await preview.getByRole('button',{name:'Validate Event',exact:true}).click();
 await publish.getByRole('button',{name:'Publish Event',exact:true}).click();
 await expect(publish).toContainText('Published 1 event(s)',{timeout:45000});
 await signOut(page);
 await signIn(page,false);
 await nav(page,'Events');
 await page.getByLabel('Search events').fill('Lantern');
 const card=page.locator('.record-card').filter({hasText:'Lantern Arts Fair'});
 await expect(card).toContainText('Reviewed Test Hall');
 await expect(card).toContainText('30');
 await card.getByText('Event details and source',{exact:true}).click();
 await expect(card).toContainText('original artwork');
 await page.getByLabel('Search events').fill('Moonlight');
 await expect(page.getByRole('region',{name:'Date not announced',exact:true})).toContainText('Moonlight Club Recruitment');
 await nav(page,'Ask CampusOS');
 await page.getByLabel('Ask a campus question').fill('Tell me about the Moonlight Club Recruitment event.');
 await page.getByRole('button',{name:'Send question',exact:true}).click();
 await expect(page.locator('.chat-answer').last()).toContainText(/sketchbook|recruitment/,{timeout:30000});
 await expect(page.getByRole('button',{name:/undated-club.txt: original restricted/})).toBeVisible();
 await page.getByLabel('Ask a campus question').fill('What is the registration fee?');
 await page.getByRole('button',{name:'Send question',exact:true}).click();
 await expect(page.locator('.chat-answer').last()).toContainText('not available');
 await expect(page.locator('.chat-answer').last()).not.toContainText('30');
 await nav(page,'Events');
 for(const width of [390,768,1440]){
  await page.setViewportSize({width,height:1000});
  await expect(page.getByRole('heading',{name:'Events',exact:true})).toBeVisible();
  await expect(page.locator('body')).toHaveJSProperty('scrollWidth',await page.locator('body').evaluate(el=>el.clientWidth));
 }
});
