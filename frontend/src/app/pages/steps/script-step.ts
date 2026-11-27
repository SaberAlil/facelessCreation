import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Script, ScriptView, Source, Task, errorText, estimateSeconds } from '../../api';

@Component({
  selector: 'app-script-step',
  imports: [FormsModule],
  template: `
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @if (!v()) {
      <div class="card">
        @if (busy()) { <p>The script is being written from your sources…</p> }
        @else {
          <h3>No script yet</h3>
          <p class="muted">The local AI writes a 65–80 second script using only the sources in your calendar.</p>
          <button class="primary big" (click)="write()">✍ Write the script</button>
        }
      </div>
    } @else {
      @let s = edit()!;
      <div class="card">
        <div class="row spread">
          <input class="title" [(ngModel)]="s.title" (ngModelChange)="touch()" />
          <span class="chip {{ lengthClass() }}">≈ {{ est().toFixed(0) }} s · {{ words() }} words</span>
        </div>
        <div class="meter"><div [style.left.%]="meterPos()"></div>
          <span style="left:0">0 s</span><span style="left:50%">45 s</span><span style="left:100%">90 s</span></div>
        <p class="small muted">{{ lengthHint() }}</p>
        @for (sc of s.scenes; track sc.id; let i = $index) {
          <div class="scene">
            <div class="small muted">Scene {{ i + 1 }} · {{ count(sc.narration) }} words</div>
            <textarea rows="3" [(ngModel)]="sc.narration" (ngModelChange)="touch()"></textarea>
          </div>
        }
        <div class="row">
          <button (click)="save()" [disabled]="!dirty()">Save changes</button>
          <button class="link" (click)="rewrite()" [disabled]="busy()">Rewrite with AI</button>
          @if (dirty()) { <span class="small warn">Unsaved changes</span> }
        </div>
        @for (e of v()!.report.errors; track e) { <div class="banner error small">{{ e }}</div> }
      </div>

      @if (v()!.report.flagged_claims.length) {
        <h2>Facts to check ({{ s.claims.length }})</h2>
        <p class="small muted">The AI only used your sources, but it can still misread them. Open the source and
          check each fact; edit the scene text if it is not supported.</p>
        <div class="card">
          @for (c of s.claims; track c.id) {
            <div class="claim">
              <div>{{ c.text }}</div>
              <div class="small">
                @for (sid of c.source_ids; track sid) {
                  @if (src(sid); as so) {
                    @if (so.url) { <a [href]="so.url" target="_blank" rel="noopener noreferrer">{{ so.title }}</a> }
                    @else { {{ so.title }} }
                  } @else { <span class="warn">no source</span> }
                } @empty { <span class="warn">no source: remove or rephrase this</span> }
              </div>
            </div>
          }
        </div>
      }

      <div class="card approve">
        @if (v()!.approved && !dirty()) {
          <span class="chip ok">✓ Approved</span>
          <span class="small muted">You can still edit the text above: saving a change redoes the voice, pictures and video.</span>
        } @else {
          <button class="primary big" (click)="approve()" [disabled]="!canApprove()">
            {{ v()!.report.flagged_claims.length ? '✓ I checked the facts — approve the script' : '✓ Approve the script' }}</button>
          <span class="small muted">{{ approveHint() }}</span>
        }
      </div>
    }
  `,
  styles: `
    .title { font-size: 1.2rem; font-weight: 700; flex: 1; min-width: 12rem; border-color: transparent; padding-left: 0; }
    .title:hover, .title:focus { border-color: var(--line); padding-left: .6rem; }
    .meter { position: relative; height: 26px; margin: .6rem 0 0;
             background: linear-gradient(90deg, var(--bad-soft) 0 50%, var(--ok-soft) 50% 95.5%, var(--bad-soft) 95.5%);
             border-radius: 6px; }
    .meter div { position: absolute; top: -3px; width: 4px; height: 32px; background: var(--fg); border-radius: 2px; transition: left .2s; }
    .meter span { position: absolute; top: 28px; transform: translateX(-50%); font-size: .75rem; color: var(--muted); }
    .meter + p { margin-top: 1.4rem; }
    .scene { margin: .6rem 0; }
    .claim { padding: .5rem 0; border-bottom: 1px solid var(--line); }
    .claim:last-child { border-bottom: none; }
    .claim a { margin-right: .6rem; }
    .approve { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
  `,
})
export class ScriptStep {
  private api = inject(Api);
  jobId = input.required<string>();
  version = input(0);
  busy = input(false);
  task = output<Task | null>();
  changed = output<void>();
  v = signal<ScriptView | null>(null);
  edit = signal<Script | null>(null);
  sources = signal<Source[]>([]);
  error = signal('');
  dirty = signal(false);
  rev = signal(0); // bumps on every keystroke so the length meter updates live

  est = computed(() => { this.rev(); const s = this.edit(); return s ? estimateSeconds(s.scenes) : 0; });
  words = computed(() => { this.rev(); const s = this.edit();
    return s ? s.scenes.reduce((a, x) => a + this.count(x.narration), 0) : 0; });
  meterPos = computed(() => Math.min(100, (this.est() / 90) * 100));
  lengthClass = computed(() => this.est() > 86 || this.est() < 45 ? 'bad' : this.est() > 80 ? 'review' : 'ok');

  constructor() {
    effect(() => { this.version(); if (!this.dirty()) this.load(); });
  }

  count(t: string) { return t.trim().split(/\s+/).filter(Boolean).length; }
  touch() { this.dirty.set(true); this.rev.update((x) => x + 1); }
  src(id: string) { return this.sources().find((s) => s.id === id); }

  lengthHint() {
    const e = this.est();
    if (e > 86) return `Too long: videos must stay under 90 s. Remove about ${Math.ceil((e - 82) * 2.2)} words.`;
    if (e < 45) return `Too short: videos must be at least 45 s. Add about ${Math.ceil((48 - e) * 2.2)} words.`;
    return 'Good length. Edit any scene directly; the estimate updates as you type.';
  }

  canApprove() {
    return !this.dirty() && !this.busy() && this.est() <= 86 && this.est() >= 45 && !this.v()!.report.errors.length;
  }

  approveHint() {
    if (this.dirty()) return 'Save your changes first.';
    if (!this.canApprove()) return 'Fix the length first.';
    return 'After approving, the voice and pictures are made automatically.';
  }

  async load() {
    try {
      this.sources.set((await this.api.research(this.jobId())).research.sources ?? []);
      const v = await this.api.script(this.jobId());
      this.v.set(v);
      this.edit.set(structuredClone(v.script));
      this.rev.update((x) => x + 1);
      this.error.set('');
    } catch (e) {
      const m = errorText(e);
      this.v.set(null);
      if (!m.includes('no script')) this.error.set(m);
    }
  }

  async save() {
    const s = this.edit();
    if (!s) return;
    try {
      const v = await this.api.saveScript(this.jobId(), s);
      this.v.set(v); this.edit.set(structuredClone(v.script)); this.dirty.set(false);
      this.changed.emit();
    } catch (e) { this.error.set(errorText(e)); }
  }

  async write() {
    try { this.task.emit(await this.api.run(this.jobId(), 'resume')); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async rewrite() {
    try { this.dirty.set(false); this.task.emit(await this.api.draft(this.jobId())); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async approve() {
    try {
      const r = await this.api.approveScript(this.jobId(), { acknowledge_flags: true });
      this.task.emit(r.task);
    } catch (e) { this.error.set(errorText(e)); }
  }
}
