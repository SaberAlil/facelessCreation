import { Component, effect, inject, input, signal } from '@angular/core';
import { Api, JobDetail, errorText } from '../../api';

@Component({
  selector: 'app-publish-step',
  template: `
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @if (job().job.state !== 'EXPORTED' || !files().length) {
      <div class="card"><p class="muted">The files to publish appear here after you approve the video.</p></div>
    } @else {
      <div class="banner ok">Ready to publish. Upload the video yourself on each platform and paste the text below.</div>
      <div class="card">
        <h3>Files</h3>
        @for (f of files(); track f.name) {
          <div class="row spread line">
            <span>{{ nice(f.name) }} <span class="small muted">{{ (f.bytes / 1e6).toFixed(1) }} MB</span></span>
            <a class="button" [href]="api.exportUrl(job().job.id, f.name)" download>⬇ Download</a>
          </div>
        }
        <p class="small muted">Also saved in <code>{{ folder() }}</code></p>
      </div>
      @for (p of platforms; track p.key) {
        @if (texts()[p.key]; as t) {
          <div class="card">
            <div class="row spread"><h3>{{ p.name }}</h3>
              <button (click)="copy(p.key, t)">{{ copied() === p.key ? '✓ Copied' : 'Copy text' }}</button></div>
            <pre>{{ t }}</pre>
          </div>
        }
      }
    }
  `,
  styles: `
    .line { padding: .45rem 0; border-bottom: 1px solid var(--line); }
    pre { white-space: pre-wrap; font: inherit; font-size: .9rem; background: var(--bg); padding: .8rem;
          border-radius: 8px; max-height: 18rem; overflow: auto; }
  `,
})
export class PublishStep {
  api = inject(Api);
  job = input.required<JobDetail>();
  version = input(0);
  files = signal<{ name: string; bytes: number }[]>([]);
  texts = signal<Record<string, string>>({});
  folder = signal('');
  copied = signal('');
  error = signal('');
  platforms = [{ key: 'youtube', name: 'YouTube Shorts' }, { key: 'tiktok', name: 'TikTok' },
               { key: 'instagram', name: 'Instagram Reels' }];

  constructor() { effect(() => { this.version(); this.load(); }); }

  nice(name: string) {
    if (name.endsWith('.mp4')) return 'Video (MP4)';
    if (name.endsWith('-cover.png')) return 'Cover image';
    if (name.endsWith('.srt')) return `Subtitles (${name.split('.').slice(-2, -1)[0]})`;
    return name;
  }

  async load() {
    if (this.job().job.state !== 'EXPORTED') return;
    try {
      const r = await this.api.exportFiles(this.job().job.id);
      this.files.set(r.files.filter((f) => !f.name.endsWith('.json') && !f.name.endsWith('.txt')));
      this.texts.set(r.texts); this.folder.set(r.folder);
    } catch (e) { this.error.set(errorText(e)); }
  }

  async copy(key: string, text: string) {
    try { await navigator.clipboard.writeText(text); this.copied.set(key); setTimeout(() => this.copied.set(''), 2000); }
    catch { this.error.set('Copy failed: select the text and copy it manually.'); }
  }
}
