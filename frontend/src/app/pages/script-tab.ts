import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Approval, ScriptView, Task, errorText } from '../api';
import { ApprovalForm } from './approval-form';

@Component({
  selector: 'app-script-tab',
  imports: [FormsModule, ApprovalForm],
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    <div class="card">
      <b>Draft</b>
      <p class="small muted">The local LLM writes only from recorded research; every claim it
        makes is flagged for your fact-check. Or paste your own script
        (<code># Title</code>, then one paragraph per scene).</p>
      <div class="row">
        <button (click)="draftLlm()">Draft with local LLM</button>
        <span class="muted small">or</span>
      </div>
      <textarea rows="6" [(ngModel)]="pasted" placeholder="# Title&#10;&#10;First scene narration…&#10;&#10;Second scene…"></textarea>
      <div class="row"><button (click)="draftText()" [disabled]="pasted.trim().length < 20">Use pasted script</button></div>
    </div>

    @if (v(); as v) {
      <div class="card">
        <div class="row">
          <input [(ngModel)]="v.script.title" style="flex:1; font-weight:600" />
          <span class="pill">{{ v.report.words }} words ≈ {{ v.report.est_seconds }}s</span>
          <span class="pill">{{ v.script.provider }}</span>
          <span class="pill" [class.ok]="v.approved">{{ v.approved ? 'approved' : 'not approved' }}</span>
        </div>
        @for (m of v.report.errors; track m) { <div class="bad small">error: {{ m }}</div> }
        @for (m of v.report.warnings; track m) { <div class="warn small">warning: {{ m }}</div> }
        <label class="small muted">Hook</label>
        <input [(ngModel)]="v.script.hook" style="width:100%" />
        @for (s of v.script.scenes; track s.id) {
          <div class="scene">
            <div class="small muted">{{ s.id }} · claims: {{ s.claim_ids.join(', ') || '—' }}</div>
            <textarea rows="2" [(ngModel)]="s.narration"></textarea>
            <div class="row small">
              <input [(ngModel)]="s.visual" placeholder="visual concept" style="flex:2" />
              <input [(ngModel)]="s.overlay" placeholder="overlay (≤5 words)" style="flex:1" />
            </div>
          </div>
        }
        <div class="row"><button class="primary" (click)="save()">Save edits</button>
          <span class="small muted">Saving changes the script hash and withdraws any approval.</span></div>
      </div>

      <h2>Claims</h2>
      @if (v.report.flagged_claims.length) {
        <div class="notice small">
          @for (c of v.report.flagged_claims; track c) { <div>⚑ {{ c }}</div> }
        </div>
      }
      <div class="table-wrap"><table class="small">
        <thead><tr><th>ID</th><th>Claim</th><th>Sources</th><th>Needs review</th></tr></thead>
        <tbody>@for (c of v.script.claims; track c.id) {
          <tr><td>{{ c.id }}</td><td>{{ c.text }}</td><td>{{ c.source_ids.join(', ') }}</td>
            <td><label><input type="checkbox" [(ngModel)]="c.needs_review" /> flagged</label></td></tr>
        }</tbody>
      </table></div>
      <p class="small muted">Clear a flag only after checking that claim against its source, then save.</p>

      <h2>Research</h2>
      @if (v.research_problems.length) {
        @for (p of v.research_problems; track p) { <div class="warn small">• {{ p }}</div> }
      } @else { <div class="ok small">No open research problems.</div> }
      @if (research(); as r) {
        <div class="table-wrap"><table class="small">
          <thead><tr><th>Source</th><th>Title</th><th>Published</th><th>URL</th></tr></thead>
          <tbody>@for (s of r.sources; track s.id) {
            <tr><td>{{ s.id }}</td><td>{{ s.title }}<div class="muted">{{ s.publisher }}</div></td>
              <td>{{ s.published || '?' }}</td>
              <td>@if (s.url) { <a [href]="s.url" target="_blank" rel="noopener noreferrer">link</a> }</td></tr>
          }</tbody>
        </table></div>
        <p class="small muted">Edit research with <code>faceless research …</code> or research.json in the job folder.</p>
      }

      <app-approval-form label="Approve research & script"
        help="Confirms you reviewed every factual claim and the sources. Narration can only be generated after this."
        [allowAcknowledge]="v.report.flagged_claims.length > 0"
        [disabled]="v.report.errors.length > 0" (approve)="approve($event)" />
    } @else {
      <p class="muted">No script yet.</p>
    }
  `,
  styles: `.scene { border-top: 1px solid var(--line); padding: .5rem 0; }`,
})
export class ScriptTab {
  private api = inject(Api);
  jobId = input.required<string>();
  version = input(0);
  changed = output<void>();
  task = output<Task>();
  v = signal<ScriptView | null>(null);
  research = signal<any>(null);
  error = signal('');
  pasted = '';

  constructor() { effect(() => { this.version(); this.load(); }); }

  async load() {
    try {
      this.research.set((await this.api.research(this.jobId())).research);
      this.v.set(await this.api.script(this.jobId()));
      this.error.set('');
    } catch (e) {
      this.v.set(null);
      const msg = errorText(e);
      if (!msg.includes('no script')) this.error.set(msg);
    }
  }

  async draftLlm() {
    try { this.task.emit(await this.api.draft(this.jobId(), { llm: true })); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async draftText() {
    try { this.task.emit(await this.api.draft(this.jobId(), { llm: false, text: this.pasted })); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async save() {
    const v = this.v();
    if (!v) return;
    try { this.v.set(await this.api.saveScript(this.jobId(), v.script)); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async approve(a: Approval) {
    try { await this.api.approveScript(this.jobId(), a); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
