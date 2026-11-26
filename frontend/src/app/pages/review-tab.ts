import { Component, effect, inject, input, output, signal } from '@angular/core';
import { Api, Approval, JobDetail, ReviewView, Task, errorText } from '../api';
import { ApprovalForm } from './approval-form';

@Component({
  selector: 'app-review-tab',
  imports: [ApprovalForm],
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    <div class="media">
      @for (f of videos; track f) {
        @if (job().files[f]) {
          <figure><figcaption class="small muted">{{ f }}</figcaption>
            <video controls preload="metadata" [src]="api.fileUrl(job().job.id, f, '' + version())"></video></figure>
        }
      }
      @if (job().files['cover.png']) {
        <figure><figcaption class="small muted">cover.png</figcaption>
          <img [src]="api.fileUrl(job().job.id, 'cover.png', '' + version())" alt="cover" /></figure>
      }
    </div>
    @if (!job().files['final.mp4'] && !job().files['preview.mp4']) {
      <p class="muted">Nothing rendered yet. Use "Preview 540p" or "Final render" in Overview.</p>
    }
    @if (r(); as r) {
      <h2>Final checklist</h2>
      <ul class="check">
        @for (i of r.checklist; track i.check) {
          <li><span [class.ok]="i.ok" [class.bad]="!i.ok && i.required" [class.warn]="!i.ok && !i.required">
            {{ i.ok ? '✓' : (i.required ? '✗' : '!') }}</span> {{ i.check }}
            @if (i.detail) { <span class="small muted">— {{ i.detail }}</span> }</li>
        }
      </ul>
      <p class="small">Watch the whole video. Check every claim, caption and visual; confirm nothing
        misrepresents real people or companies, and that platform AI-disclosure rules are followed.</p>
      @if (r.approved) {
        <div class="row"><span class="pill ok">final approved</span>
          <button class="primary" (click)="exportJob()">Export package</button></div>
      } @else {
        <app-approval-form label="Approve final video" [disabled]="blocked()"
          help="Records the hashes of the rendered MP4, captions, script and asset manifest. Refused while a required check fails."
          (approve)="approve($event)" />
      }
      @if (job().job.state === 'EXPORTED') {
        <p class="ok">Exported to <code>data/jobs/{{ job().job.id }}/export/</code>. Publish manually.</p>
      }
    }
  `,
  styles: `
    .media { display: flex; gap: 1rem; flex-wrap: wrap; }
    figure { margin: 0; }
    video, .media img { width: 240px; aspect-ratio: 9/16; background: #000; border-radius: 6px; }
    .check { list-style: none; padding: 0; } .check li { padding: .2rem 0; }
  `,
})
export class ReviewTab {
  api = inject(Api);
  job = input.required<JobDetail>();
  version = input(0);
  changed = output<void>();
  task = output<Task>();
  r = signal<ReviewView | null>(null);
  error = signal('');
  videos = ['preview.mp4', 'final.mp4'];

  constructor() { effect(() => { this.version(); this.load(); }); }

  blocked() { return (this.r()?.checklist ?? []).some((i) => i.required && !i.ok); }

  async load() {
    try { this.r.set(await this.api.review(this.job().job.id)); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async approve(a: Approval) {
    try { await this.api.approveFinal(this.job().job.id, a); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async exportJob() {
    try { this.task.emit(await this.api.run(this.job().job.id, 'export')); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
