import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header class="top">
      <a class="brand" routerLink="/jobs">Faceless&nbsp;Studio</a>
      <nav>
        <a routerLink="/calendar" routerLinkActive="active">Calendar</a>
        <a routerLink="/jobs" routerLinkActive="active">Jobs</a>
        <a routerLink="/system" routerLinkActive="active">System</a>
      </nav>
      <span class="note">Local only · publishing is manual</span>
    </header>
    <main><router-outlet /></main>
  `,
  styles: `
    .top { display: flex; gap: 1.5rem; align-items: center; padding: .75rem 1.25rem;
           background: var(--panel); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
    .brand { font-weight: 700; color: var(--fg); text-decoration: none; }
    nav { display: flex; gap: 1rem; }
    nav a { color: var(--muted); text-decoration: none; padding: .25rem 0; }
    nav a.active { color: var(--fg); border-bottom: 2px solid var(--accent); }
    .note { margin-left: auto; color: var(--muted); font-size: .85rem; }
    main { padding: 1.25rem; max-width: 1200px; margin: 0 auto; }
  `,
})
export class App {}
