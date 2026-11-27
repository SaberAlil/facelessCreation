import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, CalendarRow, LANGUAGES, errorText } from '../api';
import { stateLabel } from './labels';

interface Month { key: string; label: string; rows: CalendarRow[] }

@Component({
  selector: 'app-calendar',
  imports: [RouterLink, FormsModule],
  template: `
    <div class="row spread">
      <div><h1>Calendar</h1><p class="muted">{{ rows().length }} planned videos</p></div>
      <div class="row">
        <input [ngModel]="query()" (ngModelChange)="query.set($event)" placeholder="Search topics…" />
        <label class="small">Subtitles
          <select [(ngModel)]="subtitles">
            @for (l of langs; track l[0]) { <option [value]="l[0]">{{ l[1] }}</option> }
          </select></label>
      </div>
    </div>
    @if (error()) { <div class="banner error">{{ error() }}</div> }

    @for (m of months(); track m.key) {
      <h2>{{ m.label }}</h2>
      <div class="card list">
        @for (r of m.rows; track r.day) {
          <div class="line">
            <div class="date">{{ r.date?.slice(8) }}<span class="small muted"> {{ weekday(r.date) }}</span></div>
            <div class="topic">{{ r.topic }}
              <div class="small muted">{{ sources(r) }}</div></div>
            <div class="act">
              @if (r.job) {
                <span class="chip {{ label(r).cls }}">{{ label(r).text }}</span>
                <a class="button" [routerLink]="['/videos', r.job.id]">Open</a>
              } @else {
                <button class="primary" (click)="make(r)" [disabled]="busy()">▶ Make video</button>
              }
            </div>
          </div>
        }
      </div>
    } @empty { <p class="muted">No topics match.</p> }

    <details class="card import">
      <summary><b>Import or replace the calendar</b></summary>
      <p class="small muted">An .xlsx or .csv with a topic column. Put source links (one per line) in a
        "Research / sources" column: Wikipedia pages and research papers work best.</p>
      <div class="row">
        <input type="file" accept=".csv,.xlsx" (change)="pick($event)" />
        <label class="small"><input type="checkbox" [(ngModel)]="replace" /> replace the current calendar</label>
        <button class="primary" [disabled]="!file() || busy()" (click)="upload()">Import</button>
        <span class="small ok">{{ message() }}</span>
      </div>
    </details>
  `,
  styles: `
    .list { padding: .3rem 1rem; }
    .line { display: grid; grid-template-columns: 4.5rem 1fr auto; gap: 1rem; align-items: center;
            padding: .65rem 0; border-bottom: 1px solid var(--line); }
    .line:last-child { border-bottom: none; }
    .date { font-weight: 700; font-size: 1.1rem; }
    .act { display: flex; gap: .5rem; align-items: center; }
    @media (max-width: 640px) { .line { grid-template-columns: 3rem 1fr; } .act { grid-column: 2; } }
    .import { margin-top: 2rem; }
    summary { cursor: pointer; }
  `,
})
export class CalendarPage {
  private api = inject(Api);
  private router = inject(Router);
  rows = signal<CalendarRow[]>([]);
  query = signal('');
  file = signal<File | null>(null);
  busy = signal(false);
  error = signal('');
  message = signal('');
  replace = true;
  subtitles = 'en';
  langs = Object.entries(LANGUAGES);

  months = computed<Month[]>(() => {
    const q = this.query().trim().toLowerCase();
    const out = new Map<string, Month>();
    for (const r of this.rows()) {
      if (q && !r.topic.toLowerCase().includes(q)) continue;
      const key = (r.date ?? '').slice(0, 7) || 'undated';
      if (!out.has(key)) {
        const label = r.date ? new Date(r.date + 'T00:00:00').toLocaleDateString(undefined,
          { month: 'long', year: 'numeric' }) : 'Without date';
        out.set(key, { key, label, rows: [] });
      }
      out.get(key)!.rows.push(r);
    }
    return [...out.values()];
  });

  constructor() {
    this.load();
    this.api.settings().then((s) => (this.subtitles = s.default_subtitles)).catch(() => undefined);
  }

  async load() {
    try { this.rows.set(await this.api.calendar()); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  label(r: CalendarRow) { return stateLabel(r.job!.state); }

  weekday(d: string | null) {
    return d ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short' }) : '';
  }

  sources(r: CalendarRow): string {
    const key = Object.keys(r.extra ?? {}).find((k) => /research|source/i.test(k));
    const text = key ? String(r.extra[key] ?? '') : '';
    const n = (text.match(/https?:\/\//g) ?? []).length;
    return n ? `${n} source${n > 1 ? 's' : ''}` : 'no sources: Wikipedia will be searched';
  }

  pick(ev: Event) { this.file.set((ev.target as HTMLInputElement).files?.[0] ?? null); }

  async upload() {
    const f = this.file();
    if (!f) return;
    this.busy.set(true);
    try {
      const r = await this.api.importCalendar(f, this.replace);
      this.message.set(`Imported ${r.rows} topics.`);
      await this.load();
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }

  async make(r: CalendarRow) {
    this.busy.set(true);
    try {
      const t = await this.api.make({ day: r.day, subtitles: this.subtitles });
      await this.router.navigate(['/videos', t.job_id]);
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
