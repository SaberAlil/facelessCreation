import { Routes } from '@angular/router';
import { CalendarPage } from './pages/calendar';
import { JobsPage } from './pages/jobs';
import { JobPage } from './pages/job';
import { SystemPage } from './pages/system';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'jobs' },
  { path: 'calendar', component: CalendarPage, title: 'Calendar · Faceless Studio' },
  { path: 'jobs', component: JobsPage, title: 'Jobs · Faceless Studio' },
  { path: 'jobs/:id', component: JobPage, title: 'Job · Faceless Studio' },
  { path: 'system', component: SystemPage, title: 'System · Faceless Studio' },
  { path: '**', redirectTo: 'jobs' },
];
