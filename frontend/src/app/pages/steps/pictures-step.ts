import { Component, effect, inject, input, output, signal } from '@angular/core';
import { Api, AssetsView, SceneAsset, Script, Step, Task, errorText } from '../../api';

@Component({
  selector: 'app-pictures-step',
  template: `
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @if (!v()) {
      <div class="card"><p class="muted">{{ step().status === 'todo' ? 'Pictures are chosen after you approve the script.'
        : 'Finding a picture for each scene…' }}</p></div>
    } @else {
      <p class="muted">One picture per scene, shown for as long as that part is spoken. Real photos come from
        Wikimedia Commons and Openverse; if a picture does not fit, replace it.</p>
      <div class="grid">
        @for (a of v()!.manifest.scenes; track a.scene_id; let i = $index) {
          <div class="card pic" [class.missing]="a.kind === 'missing'">
            @if (a.kind === 'missing') {
              <div class="img empty">No picture yet</div>
            } @else if (a.kind === 'video') {
              <div class="img empty">Video clip</div>
            } @else {
              <img class="img" [src]="api.fileUrl(jobId(), 'scene-' + a.scene_id, a.library_id ?? '' + version())" alt="Scene {{ i + 1 }}" />
            }
            <div class="small"><b>Scene {{ i + 1 }}</b> · {{ sourceName(a) }}</div>
            <div class="small muted text">{{ sceneText(a.scene_id) }}</div>
            @if (a.kind !== 'missing') {
              <div class="small muted credit">
                @if (a.source_url) { <a [href]="a.source_url" target="_blank" rel="noopener noreferrer">{{ a.creator || 'source' }}</a> · }
                {{ a.license }}</div>
            }
            <div class="row actions">
              <button (click)="act(a, 'photo')" [disabled]="busy()" title="Find another real photo">↻ Another photo</button>
              <button (click)="act(a, 'ai')" [disabled]="busy()" title="Generate an AI image for this scene">✦ AI image</button>
              <label class="button" title="Use your own photo or clip">⬆ Upload
                <input type="file" accept="image/jpeg,image/png,image/webp,video/mp4" hidden (change)="upload(a, $event)" /></label>
            </div>
          </div>
        }
      </div>
      <div class="card approve">
        @if (v()!.approved) { <span class="chip ok">✓ Approved</span> }
        @else {
          <button class="primary big" (click)="approve()" [disabled]="busy() || v()!.problems.length > 0">✓ Approve the pictures</button>
          <span class="small muted">{{ v()!.problems.length ? missingText() :
            'You confirm the pictures fit the story and may be used (licences shown under each one). The video is made next.' }}</span>
        }
      </div>
    }
  `,
  styles: `
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: .9rem; }
    .pic { padding: .7rem; margin: 0; display: flex; flex-direction: column; gap: .35rem; }
    .pic.missing { border-color: var(--bad); }
    .img { width: 100%; aspect-ratio: 9 / 16; object-fit: cover; border-radius: 8px; background: var(--bg); }
    .empty { display: grid; place-items: center; color: var(--muted); }
    .text { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .credit { word-break: break-word; }
    .actions { gap: .35rem; margin-top: auto; }
    .actions button, .actions .button { padding: .35rem .55rem; font-size: .82rem; }
    .approve { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; }
  `,
})
export class PicturesStep {
  api = inject(Api);
  jobId = input.required<string>();
  version = input(0);
  busy = input(false);
  step = input.required<Step>();
  task = output<Task | null>();
  changed = output<void>();
  v = signal<AssetsView | null>(null);
  script = signal<Script | null>(null);
  error = signal('');

  constructor() { effect(() => { this.version(); this.load(); }); }

  async load() {
    try {
      this.v.set(await this.api.assets(this.jobId()));
      if (!this.script()) this.script.set((await this.api.script(this.jobId())).script);
      this.error.set('');
    } catch (e) {
      this.v.set(null);
      const m = errorText(e);
      if (!m.includes('no assets') && !m.includes('no pictures')) this.error.set(m);
    }
  }

  sceneText(id: string) { return this.script()?.scenes.find((s) => s.id === id)?.narration ?? ''; }

  sourceName(a: SceneAsset) {
    return a.kind === 'missing' ? 'missing' : ({ commons: 'Real photo', openverse: 'Real photo', comfyui: 'AI image', upload: 'Your upload',
      local: 'Your library', pexels: 'Pexels', procedural: 'Graphic' } as Record<string, string>)[a.provider] ?? a.provider;
  }

  missingText() {
    const n = this.v()!.manifest.scenes.filter((a) => a.kind === 'missing').length;
    return n ? `${n} scene(s) still need a picture: try another photo, an AI image or upload one.`
      : this.v()!.problems.join('; ');
  }

  async act(a: SceneAsset, action: 'photo' | 'ai') {
    try { this.task.emit(await this.api.picture(this.jobId(), a.scene_id, action)); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async upload(a: SceneAsset, ev: Event) {
    const f = (ev.target as HTMLInputElement).files?.[0];
    if (!f) return;
    try { this.v.set(await this.api.upload(this.jobId(), a.scene_id, f)); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async approve() {
    try { const r = await this.api.approveAssets(this.jobId(), {}); this.task.emit(r.task); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
