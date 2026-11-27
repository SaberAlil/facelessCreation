import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Caption, CaptionsView, JobDetail, LANGUAGES, ReviewView, Task, errorText } from '../../api';

@Component({
  selector: 'app-video-step',
  imports: [FormsModule],
  template: `
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @if (!job().files['final.mp4']) {
      <div class="card"><p class="muted">{{ job().steps[2].status === 'todo'
        ? 'The video is made after you approve the pictures.' : 'Making the video…' }}</p></div>
    } @else {
      <div class="layout">
        <div>
          <video controls preload="metadata" [src]="api.fileUrl(job().job.id, 'final.mp4', '' + version())"></video>
          <div class="row" style="margin-top:.5rem">
            <a class="button" [href]="api.fileUrl(job().job.id, 'final.mp4')" download>⬇ Download MP4</a>
          </div>
          <p class="small muted">No sound? Some built-in players (for example VS Code's preview or Chromium on
            Linux) cannot play AAC audio. Open the page in Chrome, Edge or Firefox, or play the downloaded file
            in VLC.</p>
        </div>
        <div class="card subs">
          <div class="row spread">
            <h3>Subtitles · {{ langName() }}</h3>
            @if (dirty()) { <button class="primary" (click)="saveSubs()" [disabled]="busy()">Save &amp; redo video</button> }
          </div>
          @if (c()?.track?.translated_by) {
            <p class="small muted">Translated automatically from the English narration: read them while you
              watch and fix any wording.</p>
          }
          @if (c()?.track && !c()!.track!.burn) { <p class="small muted">No subtitles are burned into this video.</p> }
          <div class="sublist">
            @for (cap of caps(); track cap.index) {
              <div class="sub">
                <span class="small muted">{{ fmt(cap.start) }}</span>
                <textarea rows="2" [(ngModel)]="cap.text" (ngModelChange)="dirty.set(true)"></textarea>
              </div>
            }
          </div>
        </div>
      </div>

      @if (r(); as r) {
        @if (tooLong(); as len) {
          <div class="banner warn">
            <b>The video is {{ len }} long; the limit is 90 s.</b>
            <div class="row" style="margin-top:.6rem">
              <button class="primary" (click)="fit()" [disabled]="busy()">⚡ Make it fit automatically</button>
              <button (click)="goto.emit('script')">✎ Shorten the script</button>
            </div>
            <div class="small" style="margin-top:.4rem">"Make it fit" records the voice up to 8% faster (only works if it is
              slightly too long) and keeps your pictures. Shortening the script redoes the voice, pictures and video.</div>
          </div>
        }
        @if (otherBlocking().length) {
          <div class="banner error"><b>Before you can approve:</b>
            @for (i of otherBlocking(); track i.check) { <div>• {{ i.check }}{{ i.detail ? ' — ' + i.detail : '' }}</div> }</div>
        }
        <div class="card approve">
          @if (r.approved) { <span class="chip ok">✓ Approved</span> }
          @else {
            <button class="primary big" (click)="approve()" [disabled]="busy() || blocking().length > 0 || dirty()">
              ✓ Approve the video</button>
            <span class="small muted">{{ dirty() ? 'Save the subtitle changes first.' :
              'Watch it to the end first. Approving prepares the files to publish.' }}</span>
          }
        </div>
      }
    }
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(240px, 340px) 1fr; gap: 1.2rem; align-items: start; }
    @media (max-width: 760px) { .layout { grid-template-columns: 1fr; } }
    video { width: 100%; aspect-ratio: 9 / 16; background: #000; border-radius: 12px; }
    .subs { margin: 0; }
    .sublist { max-height: 60vh; overflow-y: auto; }
    .sub { display: grid; grid-template-columns: 3.4rem 1fr; gap: .5rem; align-items: start; margin: .3rem 0; }
    .sub textarea { min-height: 2.6rem; }
    .approve { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; }
  `,
})
export class VideoStep {
  api = inject(Api);
  job = input.required<JobDetail>();
  version = input(0);
  busy = input(false);
  task = output<Task | null>();
  changed = output<void>();
  goto = output<string>();
  c = signal<CaptionsView | null>(null);
  caps = signal<Caption[]>([]);
  r = signal<ReviewView | null>(null);
  error = signal('');
  dirty = signal(false);

  constructor() { effect(() => { this.version(); if (!this.dirty()) this.load(); }); }

  langName() { return LANGUAGES[this.job().options.subtitles] ?? this.job().options.subtitles; }
  blocking() { return (this.r()?.checklist ?? []).filter((i) => i.required && !i.ok); }
  private isLength(check: string) { return check.startsWith('duration'); }
  otherBlocking() { return this.blocking().filter((i) => !this.isLength(i.check)); }
  tooLong(): string {
    const i = this.blocking().find((x) => this.isLength(x.check));
    return i && parseFloat(i.detail) > 60 ? i.detail : '';
  }

  async fit() {
    try { this.task.emit(await this.api.run(this.job().job.id, 'fit')); }
    catch (e) { this.error.set(errorText(e)); }
  }
  fmt(t: number) { const m = Math.floor(t / 60); return `${m}:${(t % 60).toFixed(0).padStart(2, '0')}`; }

  async load() {
    try {
      const id = this.job().job.id;
      const c = await this.api.captions(id);
      this.c.set(c);
      this.caps.set((c.track?.captions ?? []).map((x) => ({ ...x })));
      this.r.set(await this.api.review(id));
      this.error.set('');
    } catch (e) { this.error.set(errorText(e)); }
  }

  async saveSubs() {
    try {
      const id = this.job().job.id;
      await this.api.saveCaptions(id, this.caps());
      this.dirty.set(false);
      this.task.emit(await this.api.run(id, 'resume')); // re-render with the corrected subtitles
    } catch (e) { this.error.set(errorText(e)); }
  }

  async approve() {
    try { const r = await this.api.approveFinal(this.job().job.id, {}); this.task.emit(r.task); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
