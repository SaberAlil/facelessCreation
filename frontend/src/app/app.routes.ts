import { Routes } from '@angular/router';
import { HomePage } from './pages/home';
import { CalendarPage } from './pages/calendar';
import { VideosPage } from './pages/videos';
import { JobPage } from './pages/job';
import { SettingsPage } from './pages/settings';

export const routes: Routes = [
  { path: '', component: HomePage, title: 'Faceless Studio' },
  { path: 'calendar', component: CalendarPage, title: 'Calendar · Faceless Studio' },
  { path: 'videos', component: VideosPage, title: 'Videos · Faceless Studio' },
  { path: 'videos/:id', component: JobPage, title: 'Video · Faceless Studio' },
  { path: 'jobs/:id', redirectTo: 'videos/:id' },
  { path: 'settings', component: SettingsPage, title: 'Settings · Faceless Studio' },
  { path: '**', redirectTo: '' },
];
