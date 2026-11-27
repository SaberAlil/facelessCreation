import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header class="top">
      <a class="brand" routerLink="/">▶ Faceless Studio</a>
      <nav>
        <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Home</a>
        <a routerLink="/calendar" routerLinkActive="active">Calendar</a>
        <a routerLink="/videos" routerLinkActive="active">Videos</a>
        <a routerLink="/settings" routerLinkActive="active">Settings</a>
      </nav>
    </header>
    <main><router-outlet /></main>
  `,
  styles: `
    .top { display: flex; gap: 2rem; align-items: center; padding: .8rem 1.5rem;
           background: var(--panel); border-bottom: 1px solid var(--line); flex-wrap: wrap;
           position: sticky; top: env(safe-area-inset-top, 0px); z-index: 10; }
    .brand { font-weight: 800; color: var(--fg); text-decoration: none; letter-spacing: .01em; }
    nav { display: flex; gap: 1.25rem; flex-wrap: wrap; }
    nav a { color: var(--muted); text-decoration: none; padding: .3rem 0; font-weight: 500; }
    nav a.active { color: var(--fg); box-shadow: inset 0 -2px 0 var(--accent); }
    main { padding: 1.5rem 1.25rem 4rem; max-width: 1150px; margin: 0 auto; }
  `,
})
export class App {}
