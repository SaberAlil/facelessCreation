import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, JobRow, LANGUAGES, errorText } from '../api';
import { stateLabel } from './labels';

@Component({
  selector: 'app-videos',
  imports: [RouterLink, FormsModule],
  template: `
    <h1>Videos</h1>
    <div class="card">
      <h3>Make a video about any topic</h3>
      <div class="row">
        <input [(ngModel)]="topic" placeholder="e.g. Why airlines overbook flights" style="flex:1; min-width: 14rem" />
        <select [(ngModel)]="subtitles" title="Subtitle language">
          @for (l of langs; track l[0]) { <option [value]="l[0]">{{ l[1] }}</option> }
        </select>
        <button class="primary" [disabled]="topic.trim().length < 3 || busy()" (click)="make()">▶ Make video</button>
      </div>
      <p class="small muted">Topics from your calendar get their sources from the spreadsheet; a free topic is
        researched on Wikipedia first.</p>
    </div>
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @for (j of jobs(); track j.id) {
      <a class="item card" [routerLink]="['/videos', j.id]">
        <div>
          <b>{{ j.topic }}</b>
          <div class="small muted">{{ j.date ?? 'free topic' }}{{ j.day ? ' · day ' + j.day : '' }}</div>
        </div>
        <span class="chip {{ label(j).cls }}">{{ label(j).text }}</span>
      </a>
    } @empty { <p class="muted">No videos yet.</p> }
  `,
  styles: `
    .item { display: flex; justify-content: space-between; gap: 1rem; align-items: center;
            text-decoration: none; color: var(--fg); margin-bottom: .6rem; }
    .item:hover { border-color: var(--accent); }
  `,
})
export class VideosPage {
  private api = inject(Api);
  private router = inject(Router);
  jobs = signal<JobRow[]>([]);
  error = signal('');
  busy = signal(false);
  topic = '';
  subtitles = 'en';
  langs = Object.entries(LANGUAGES);

  constructor() {
    this.api.jobs().then((j) => this.jobs.set(j.slice().reverse())).catch((e) => this.error.set(errorText(e)));
    this.api.settings().then((s) => (this.subtitles = s.default_subtitles)).catch(() => undefined);
  }

  label(j: JobRow) { return stateLabel(j.state); }

  async make() {
    this.busy.set(true);
    try {
      const t = await this.api.make({ topic: this.topic.trim(), subtitles: this.subtitles });
      await this.router.navigate(['/videos', t.job_id]);
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
