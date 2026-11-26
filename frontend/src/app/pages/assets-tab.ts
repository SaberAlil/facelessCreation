import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Approval, AssetsView, errorText } from '../api';
import { ApprovalForm } from './approval-form';

@Component({
  selector: 'app-assets-tab',
  imports: [FormsModule, ApprovalForm],
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (v(); as v) {
      <div class="row">
        <span class="pill" [class.ok]="v.approved">{{ v.approved ? 'asset set approved' : 'not approved' }}</span>
        @if (!v.fresh) { <span class="pill warn">stale: script changed — re-run Visuals</span> }
        <button (click)="setRights('confirmed', null)">Confirm rights for all</button>
      </div>
      @if (v.problems.length) {
        <div class="notice small">@for (p of v.problems; track p) { <div>• {{ p }}</div> }</div>
      }
      <div class="grid">
        @for (a of v.manifest.scenes; track a.scene_id) {
          <div class="card">
            @if (a.kind !== 'video') {
              <img [src]="api.fileUrl(jobId(), 'scene-' + a.scene_id, '' + version())" alt="scene {{ a.scene_id }} visual" />
            } @else { <div class="video-ph">video clip</div> }
            <div class="small"><b>{{ a.scene_id }}</b> · {{ a.provider }} {{ a.template || a.library_id }}
              @if (a.ai_generated) { <span class="pill warn">AI</span> }</div>
            <div class="small muted">{{ a.license }}</div>
            @if (a.creator || a.attribution) { <div class="small">{{ a.attribution || a.creator }}</div> }
            @if (a.source_url) { <div class="small"><a [href]="a.source_url" target="_blank" rel="noopener noreferrer">source</a></div> }
            <div class="small">commercial use: <b [class.bad]="!a.commercial_use">{{ a.commercial_use ? 'yes' : 'NO' }}</b>
              · rights: <b [class.ok]="a.rights === 'confirmed'" [class.bad]="a.rights === 'rejected'">{{ a.rights }}</b></div>
            <div class="row small">
              <button (click)="setRights('confirmed', [a.scene_id])">Confirm</button>
              <button (click)="setRights('rejected', [a.scene_id])">Reject</button>
            </div>
            <div class="row small">
              <input [(ngModel)]="overlays[a.scene_id]" placeholder="graphic text" style="flex:1; min-width:6rem" />
              <button (click)="replace(a.scene_id)" title="Replace with an original graphic">Use graphic</button>
            </div>
          </div>
        }
      </div>
      <app-approval-form label="Approve asset set"
        help="Confirms licenses allow commercial use on YouTube, TikTok and Instagram, attribution is recorded, and no visual misrepresents real people or companies."
        [disabled]="v.problems.length > 0" (approve)="approve($event)" />
    } @else {
      <p class="muted">No assets yet. Approve the script, then run "Visuals" in Overview.</p>
    }
  `,
  styles: `
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: .75rem; margin-top: .75rem; }
    .grid img, .video-ph { width: 100%; aspect-ratio: 9/16; object-fit: cover; border-radius: 6px;
      background: var(--bg); display: grid; place-items: center; }
  `,
})
export class AssetsTab {
  api = inject(Api);
  jobId = input.required<string>();
  version = input(0);
  changed = output<void>();
  v = signal<AssetsView | null>(null);
  error = signal('');
  overlays: Record<string, string> = {};

  constructor() { effect(() => { this.version(); this.load(); }); }

  async load() {
    try { this.v.set(await this.api.assets(this.jobId())); this.error.set(''); }
    catch (e) { this.v.set(null); const m = errorText(e); if (!m.includes('no assets')) this.error.set(m); }
  }

  async setRights(status: string, ids: string[] | null) {
    try { this.v.set(await this.api.rights(this.jobId(), status, ids)); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async replace(sceneId: string) {
    try {
      this.v.set(await this.api.replaceAsset(this.jobId(), {
        scene_id: sceneId, procedural: true, overlay: this.overlays[sceneId] ?? undefined }));
      this.changed.emit();
    } catch (e) { this.error.set(errorText(e)); }
  }

  async approve(a: Approval) {
    try { await this.api.approveAssets(this.jobId(), a); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
