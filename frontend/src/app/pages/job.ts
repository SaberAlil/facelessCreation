import { Component, OnDestroy, effect, inject, input, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Api, JobDetail, Task, errorText } from '../api';
import { ScriptTab } from './script-tab';
import { AssetsTab } from './assets-tab';
import { CaptionsTab } from './captions-tab';
import { ReviewTab } from './review-tab';

type Tab = 'overview' | 'script' | 'assets' | 'captions' | 'review';

const ACTIONS: { stage: string; label: string; help: string }[] = [
  { stage: 'voice', label: 'Narration', help: 'Kokoro TTS on CPU' },
  { stage: 'assets', label: 'Visuals', help: 'library → original graphics' },
  { stage: 'subtitles', label: 'Captions', help: 'faster-whisper CPU INT8' },
  { stage: 'subtitles-approximate', label: 'Approx. captions', help: 'fallback; needs review' },
  { stage: 'preview', label: 'Preview 540p', help: 'fast check render' },
  { stage: 'render', label: 'Final render', help: '1080x1920 H.264/AAC' },
  { stage: 'export', label: 'Export', help: 'after final approval' },
  { stage: 'resume', label: 'Run automatically', help: 'everything up to the next approval' },
];

@Component({
  selector: 'app-job',
  imports: [RouterLink, DecimalPipe, ScriptTab, AssetsTab, CaptionsTab, ReviewTab],
  template: `
    <p class="small"><a routerLink="/jobs">← Jobs</a></p>
    @if (d(); as d) {
      <h1>{{ d.title || d.job.topic }}</h1>
      <div class="row small muted">
        <code>{{ d.job.id }}</code>
        @if (d.job.day) { <span>day {{ d.job.day }} {{ d.job.date }}</span> }
        <span class="pill {{ d.job.state }}">{{ d.job.state }}</span>
        @if (d.timing) { <span>narration {{ d.timing.duration | number: '1.1-1' }}s
          ({{ d.timing.provider }}{{ d.timing.demo ? ', DEMO' : '' }})</span> }
      </div>
      <div class="notice">
        <div class="row">
          <span><b>Next:</b> {{ d.gate.reason }}</span>
          @if (!d.gate.done && !isApproval(d.gate.reason)) {
            <button class="primary" (click)="run('resume')" [disabled]="busy()">
              Run automatically to the next approval</button>
          }
          @if (isApproval(d.gate.reason)) {
            <span class="small">→ open the <b>{{ approvalTab(d.gate.reason) }}</b> tab to review and approve,
              then press "Run automatically" again.</span>
          }
        </div>
        @if (d.job.failed_stage) { <div class="bad small">Last {{ d.job.failed_stage }} run failed: {{ d.job.error }}</div> }
      </div>
      @if (task(); as t) {
        <div class="card small">
          Task <b>{{ t.action }}</b>: {{ t.status }}
          @if (t.status === 'running' || t.status === 'queued') { … }
          @if (t.error) { <div class="error">{{ t.error }}{{ t.hint ? ' — ' + t.hint : '' }}</div> }
        </div>
      }
      @if (error()) { <div class="error">{{ error() }}</div> }

      <nav class="tabs">
        @for (t of tabs; track t.id) {
          <button [class.active]="tab() === t.id" (click)="tab.set(t.id)">{{ t.label }}</button>
        }
      </nav>

      @switch (tab()) {
        @case ('overview') {
          <h2>Run a stage</h2>
          <div class="actions">
            @for (a of actions; track a.stage) {
              <button (click)="run(a.stage)" [disabled]="busy()" [title]="a.help">{{ a.label }}</button>
            }
          </div>
          <p class="small muted">Stages run one at a time on the server. Approvals are never
            automatic: use the Script, Assets and Review tabs.</p>
          <h2>Status</h2>
          <div class="row small">
            @for (k of keys(d.approvals); track k) {
              <span class="pill">{{ k }} approval {{ d.approvals[k] ? '✓' : '✗' }}</span> }
            @for (k of keys(d.fresh); track k) {
              <span class="pill">{{ k }} {{ d.fresh[k] ? 'current' : 'missing/stale' }}</span> }
          </div>
          <h2>Stage runs (time, device, memory)</h2>
          <div class="table-wrap">
            <table class="small">
              <thead><tr><th>Stage</th><th>Status</th><th>Started</th><th>Secs</th><th>Device</th>
                <th>RSS MiB</th><th>Child MiB</th><th>GPU used MiB</th><th>Fallbacks / error</th></tr></thead>
              <tbody>
                @for (r of d.runs.slice().reverse(); track r.id) {
                  <tr>
                    <td>{{ r.stage }}</td>
                    <td [class.bad]="r.status === 'failed'">{{ r.status }}</td>
                    <td>{{ r.started }}</td><td>{{ r.duration_s | number: '1.1-1' }}</td>
                    <td>{{ r.device }}</td><td>{{ r.peak_rss_mib }}</td><td>{{ r.peak_children_rss_mib }}</td>
                    <td>{{ r.peak_vram_used_mib }}</td>
                    <td>{{ r.fallbacks.join('; ') }} <span class="bad">{{ r.error }}</span></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
        @case ('script') { <app-script-tab [jobId]="d.job.id" [version]="version()" (changed)="refresh()" (task)="track($event)" /> }
        @case ('assets') { <app-assets-tab [jobId]="d.job.id" [version]="version()" (changed)="refresh()" /> }
        @case ('captions') { <app-captions-tab [jobId]="d.job.id" [version]="version()" (changed)="refresh()" /> }
        @case ('review') { <app-review-tab [job]="d" [version]="version()" (changed)="refresh()" (task)="track($event)" /> }
      }
    } @else if (error()) {
      <div class="error">{{ error() }}</div>
    }
  `,
  styles: `
    .tabs { display: flex; gap: .25rem; margin: 1rem 0; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
    .tabs button { border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none; }
    .tabs button.active { border-bottom-color: var(--accent); font-weight: 600; }
    .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  `,
})
export class JobPage implements OnDestroy {
  private api = inject(Api);
  id = input.required<string>();
  d = signal<JobDetail | null>(null);
  task = signal<Task | null>(null);
  busy = signal(false);
  error = signal('');
  tab = signal<Tab>('overview');
  version = signal(0);
  actions = ACTIONS;
  tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' }, { id: 'script', label: 'Script & research' },
    { id: 'assets', label: 'Assets' }, { id: 'captions', label: 'Captions' },
    { id: 'review', label: 'Preview, review & export' },
  ];
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => { this.id(); this.refresh(); this.resumeTracking(); });
  }

  ngOnDestroy() { if (this.timer) clearTimeout(this.timer); }

  keys(o: Record<string, boolean>) { return Object.keys(o); }

  isApproval(reason: string) { return /review|approval/i.test(reason); }

  approvalTab(reason: string) {
    if (/script/i.test(reason)) return 'Script & research';
    if (/asset|rights/i.test(reason)) return 'Assets';
    return 'Preview, review & export';
  }

  async refresh() {
    try {
      this.d.set(await this.api.job(this.id()));
      this.version.update((v) => v + 1);
      this.error.set('');
    } catch (e) { this.error.set(errorText(e)); }
  }

  private async resumeTracking() {
    try {
      const running = (await this.api.tasks(this.id())).find(
        (t) => t.status === 'running' || t.status === 'queued');
      if (running) this.track(running);
    } catch { /* not fatal */ }
  }

  async run(stage: string) {
    this.error.set('');
    try { this.track(await this.api.run(this.id(), stage)); }
    catch (e) { this.error.set(errorText(e)); }
  }

  track(t: Task) {
    this.task.set(t);
    this.busy.set(t.status === 'queued' || t.status === 'running');
    if (this.timer) clearTimeout(this.timer);
    if (this.busy()) {
      this.timer = setTimeout(async () => {
        try { this.track(await this.api.task(t.id)); }
        catch (e) { this.error.set(errorText(e)); this.busy.set(false); }
      }, 1500);
    } else {
      this.refresh();
    }
  }
}
