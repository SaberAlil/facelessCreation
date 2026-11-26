import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, CalendarRow, errorText } from '../api';

@Component({
  selector: 'app-calendar',
  imports: [RouterLink, FormsModule],
  template: `
    <h1>Content calendar</h1>
    <div class="card row">
      <label>Import CSV / XLSX <input type="file" accept=".csv,.xlsx" (change)="pick($event)" /></label>
      <label class="small"><input type="checkbox" [(ngModel)]="replace" /> replace existing</label>
      <button class="primary" [disabled]="!file() || busy()" (click)="upload()">Import</button>
      <span class="muted small">{{ message() }}</span>
    </div>
    @if (error()) { <div class="error">{{ error() }}</div> }
    <div class="row" style="margin-bottom:.5rem">
      <label>Days <input type="number" min="1" [(ngModel)]="from" style="width:5rem" /></label>
      <label>to <input type="number" min="1" [(ngModel)]="to" style="width:5rem" /></label>
      <button (click)="load()">Show</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Day</th><th>Date</th><th>Topic</th><th>Job</th></tr></thead>
        <tbody>
          @for (r of rows(); track r.day) {
            <tr>
              <td>{{ r.day }}</td>
              <td>{{ r.date ?? '' }}</td>
              <td>{{ r.topic }}</td>
              <td>
                @if (r.job) {
                  <a [routerLink]="['/jobs', r.job.id]">open</a>
                  <span class="pill {{ r.job.state }}">{{ r.job.state }}</span>
                } @else {
                  <button (click)="create(r)" [disabled]="busy()">Create job</button>
                }
              </td>
            </tr>
          } @empty {
            <tr><td colspan="4" class="muted">No calendar rows. Import a file above.</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class CalendarPage {
  private api = inject(Api);
  private router = inject(Router);
  rows = signal<CalendarRow[]>([]);
  file = signal<File | null>(null);
  busy = signal(false);
  error = signal('');
  message = signal('');
  replace = false;
  from = 1;
  to = 31;

  constructor() { this.load(); }

  async load() {
    try { this.rows.set(await this.api.calendar(this.from, this.to)); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  pick(ev: Event) {
    const f = (ev.target as HTMLInputElement).files?.[0] ?? null;
    this.file.set(f);
  }

  async upload() {
    const f = this.file();
    if (!f) return;
    this.busy.set(true);
    try {
      const r = await this.api.importCalendar(f, this.replace);
      this.message.set(`imported ${r.rows} rows`);
      await this.load();
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }

  async create(r: CalendarRow) {
    this.busy.set(true);
    try {
      const j = await this.api.createJob({ day: r.day });
      await this.router.navigate(['/jobs', j.id]);
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
