import { Component, OnDestroy, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, JobDetail, LANGUAGES, Task, VOICES, errorText } from '../api';
import { ScriptStep } from './steps/script-step';
import { PicturesStep } from './steps/pictures-step';
import { VideoStep } from './steps/video-step';
import { PublishStep } from './steps/publish-step';

@Component({
  selector: 'app-job',
  imports: [RouterLink, FormsModule, ScriptStep, PicturesStep, VideoStep, PublishStep],
  template: `
    <p class="small"><a routerLink="/videos">← All videos</a></p>
    @if (d(); as d) {
      <div class="row spread head">
        <div>
          <h1>{{ d.title || d.job.topic }}</h1>
          <div class="small muted">{{ d.job.date ?? 'free topic' }}{{ d.job.day ? ' · day ' + d.job.day : '' }}
            @if (d.timing) { · {{ d.timing.duration.toFixed(0) }} s }</div>
        </div>
        <div class="opts">
          <label class="small">Voice
            <span class="row">
              <select [ngModel]="d.options.voice" (ngModelChange)="setVoice($event)" [disabled]="!!running()">
                @for (v of voices; track v[0]) { <option [value]="v[0]">{{ v[1] }}</option> }
              </select>
              <button class="small" (click)="listen(d.options.voice)" [disabled]="!!playing()"
                      title="Play a short sample of this voice">{{ playing() ? '…' : '▶' }}</button>
            </span>
          </label>
          <label class="small">Subtitles
            <select [ngModel]="d.options.subtitles" (ngModelChange)="setSubtitles($event)" [disabled]="!!running()">
              @for (l of langs; track l[0]) { <option [value]="l[0]">{{ l[1] }}</option> }
            </select>
          </label>
        </div>
      </div>

      <ol class="stepper">
        @for (s of d.steps; track s.key; let i = $index) {
          <li [class]="s.status" [class.current]="panel() === s.key" (click)="panel.set(s.key)">
            <span class="num">{{ s.status === 'done' ? '✓' : i + 1 }}</span>
            <span><b>{{ s.title }}</b><br /><span class="small">{{ statusText(s.status) }}</span></span>
          </li>
        }
      </ol>

      @if (running(); as t) {
        <div class="card working">
          <div class="row spread"><b>{{ t.progress?.message || 'Starting…' }}</b>
            @if (t.progress?.total) { <span class="small muted">{{ t.progress!.done }} / {{ t.progress!.total }}</span> }</div>
          <div class="progress" [class.indeterminate]="!t.progress?.total">
            <div [style.width.%]="pct(t)"></div></div>
          <p class="small muted">You can leave this page; the work continues.</p>
        </div>
      } @else if (failed(); as f) {
        <div class="banner error"><b>Something went wrong:</b> {{ f }}
          <div style="margin-top:.5rem"><button (click)="cont()">Try again</button></div></div>
      }
      @if (error()) { <div class="banner error">{{ error() }}</div> }

      @switch (panel()) {
        @case ('script') { <app-script-step [jobId]="d.job.id" [version]="version()" [busy]="!!running()"
                            (task)="track($event)" (changed)="refresh()" /> }
        @case ('pictures') { <app-pictures-step [jobId]="d.job.id" [version]="version()" [busy]="!!running()"
                              [step]="d.steps[1]" (task)="track($event)" (changed)="refresh()" /> }
        @case ('video') { <app-video-step [job]="d" [version]="version()" [busy]="!!running()"
                           (task)="track($event)" (changed)="refresh()" (goto)="panel.set($event)" /> }
        @case ('publish') { <app-publish-step [job]="d" [version]="version()" /> }
      }
    } @else if (error()) {
      <div class="banner error">{{ error() }}</div>
    }
  `,
  styles: `
    .head { align-items: flex-start; margin-bottom: 1rem; }
    .opts { display: flex; gap: .8rem; flex-wrap: wrap; align-items: flex-end; }
    .opts .row { gap: .3rem; flex-wrap: nowrap; }
    .stepper { list-style: none; padding: 0; margin: 0 0 1.25rem; display: grid;
               grid-template-columns: repeat(4, 1fr); gap: .5rem; }
    .stepper li { display: flex; gap: .6rem; align-items: center; padding: .7rem .8rem; cursor: pointer;
                  border-radius: 10px; background: var(--panel); border: 1px solid var(--line); color: var(--muted); }
    .stepper li.current { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); color: var(--fg); }
    .num { width: 1.8rem; height: 1.8rem; border-radius: 50%; display: grid; place-items: center;
           background: var(--bg); font-weight: 700; flex: none; }
    .stepper li.done .num { background: var(--ok-soft); color: var(--ok); }
    .stepper li.review .num { background: var(--warn-soft); color: var(--warn); }
    .stepper li.working .num { background: var(--accent-soft); color: var(--accent); }
    @media (max-width: 640px) { .stepper { grid-template-columns: repeat(2, 1fr); } }
    .working { border-left: 4px solid var(--accent); }
    .working .progress { margin: .6rem 0 .2rem; }
  `,
})
export class JobPage implements OnDestroy {
  private api = inject(Api);
  id = input.required<string>();
  d = signal<JobDetail | null>(null);
  task = signal<Task | null>(null);
  error = signal('');
  version = signal(0);
  panel = signal<string>('script');
  langs = Object.entries(LANGUAGES);
  voices = Object.entries(VOICES);
  playing = signal('');
  private timer: ReturnType<typeof setTimeout> | null = null;
  private chosePanel = false;

  running = computed(() => {
    const t = this.task();
    return t && (t.status === 'queued' || t.status === 'running') ? t : null;
  });
  failed = computed(() => {
    const t = this.task();
    if (t && t.status === 'failed') return (t.error ?? '') + (t.hint ? ' — ' + t.hint : '');
    const j = this.d()?.job;
    return j?.state === 'FAILED' && !this.running() ? `${j.failed_stage}: ${j.error}` : '';
  });

  constructor() {
    effect(() => { this.id(); this.chosePanel = false; this.refresh(); });
  }

  ngOnDestroy() { if (this.timer) clearTimeout(this.timer); }

  statusText(s: string) {
    return { todo: 'Not yet', working: 'Working…', review: 'Your turn', done: 'Done' }[s] ?? s;
  }

  pct(t: Task) {
    const p = t.progress;
    return p && p.total ? Math.round((100 * p.done) / p.total) : 35;
  }

  async refresh() {
    try {
      const d = await this.api.job(this.id());
      const wasRunning = !!this.running();
      this.d.set(d);
      if (d.task) this.task.set(d.task);
      if (!this.chosePanel || (wasRunning && !this.running())) {
        const cur = d.steps.find((s) => s.status !== 'done');
        this.panel.set(cur ? cur.key : 'publish');
        this.chosePanel = true;
      }
      this.version.update((v) => v + 1);
      this.error.set('');
    } catch (e) { this.error.set(errorText(e)); }
    this.schedule();
  }

  private schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => (this.running() ? this.poll() : this.refresh()), this.running() ? 1500 : 8000);
  }

  private async poll() {
    const t = this.running();
    if (!t) return this.refresh();
    try {
      const nt = await this.api.task(t.id);
      this.task.set(nt);
      if (nt.status === 'done' || nt.status === 'failed') return this.refresh();
    } catch (e) { this.error.set(errorText(e)); }
    this.schedule();
  }

  track(t: Task | null) {
    if (!t) { this.refresh(); return; }
    this.task.set(t);
    this.schedule();
  }

  async cont() {
    try { this.track(await this.api.run(this.id(), 'resume')); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async setVoice(voice: string) {
    try {
      await this.api.setOptions(this.id(), { voice });
      const d = this.d();
      // Narration already recorded: record it again with the new voice, then redo the video
      if (d && d.steps[0].status === 'done') await this.cont();
      else await this.refresh();
    } catch (e) { this.error.set(errorText(e)); }
  }

  listen(voice: string) {
    // The first sample of a voice is made on demand (a few seconds), then cached.
    this.playing.set(voice);
    const a = new Audio(this.api.voiceSampleUrl(voice));
    a.onended = a.onerror = () => this.playing.set('');
    a.play().catch(() => this.playing.set(''));
  }

  async setSubtitles(lang: string) {
    try {
      await this.api.setOptions(this.id(), { subtitles: lang });
      const d = this.d();
      // Subtitles already made: redo subtitles + video now
      if (d && (d.steps[2].status !== 'todo')) await this.cont();
      else await this.refresh();
    } catch (e) { this.error.set(errorText(e)); }
  }
}
