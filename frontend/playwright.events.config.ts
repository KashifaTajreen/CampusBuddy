import {defineConfig} from '@playwright/test';
import base from './playwright.config';
if(!process.env.CAMPUS_TEST_EVENTS_FRONTEND_DIR) throw new Error('Use scripts.verify_events_browser; an isolated frontend directory is required.');
const servers=base.webServer as {command:string;cwd?:string;[key:string]:unknown}[];
const isolated=process.env.CAMPUS_TEST_EVENTS_FRONTEND_DIR.replaceAll('\\','/');
const next=process.cwd().replaceAll('\\','/')+'/node_modules/next/dist/bin/next';
export default defineConfig({...base,
  testMatch:'events-phase2.spec.ts',
  testIgnore:[],
  projects:base.projects?.filter(p=>p.name==='desktop'),
  reporter:[['list'],['json',{outputFile:'../artifacts/events-browser-tests.json'}]],
  webServer:[{...servers[0],command:servers[0].command+` "${isolated}"`},{...servers[1],cwd:isolated,
    command:`node "${next}" dev "${isolated}" --webpack --hostname 127.0.0.1 --port 3102`,
    env:{...(servers[1].env as Record<string,string>),NEXT_PRIVATE_DEV_DIR:isolated}}],
});
