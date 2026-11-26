import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Caption, CaptionsView, errorText } from '../api';

@Component({
  selector: 'app-captions-tab',
  imports: [FormsModule],
  template: `
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (v()?.track; as t) {
      <div class="row small">
        <span class="pill">burned-in language: {{ t.language }}</span>
        @if (t.translated_by) { <span class="pill">translated by {{ t.translated_by }} from the English narration</span> }
        <span class="pill">timing: {{ t.source }} {{ t.model }}</span>
        @if (t.divergence_ratio !== null) { <span class="pill">script match {{ (t.divergence_ratio * 100).toFixed(1) }}%</span> }
        @if (t.approximate) { <span class="pill warn">approximate timing</span> }
        <span class="pill" [class.ok]="t.reviewed">{{ t.reviewed ? 'reviewed' : 'not reviewed' }}</span>
        @if (!v()?.fresh) { <span class="pill warn">stale: narration changed</span> }
      </div>
      @if (t.divergences.length) {
        <div class="notice small">Transcript differs from script (captions use the script text):
          @for (d of t.divergences; track d) { <div>• {{ d }}</div> }</div>
      }
      <div class="table-wrap"><table class="small">
        <thead><tr><th>#</th><th>Start (s)</th><th>End (s)</th><th>Text</th></tr></thead>
        <tbody>@for (c of rows(); track c.index) {
          <tr><td>{{ c.index }}</td>
            <td><input type="number" step="0.01" [(ngModel)]="c.start" style="width:6rem" /></td>
            <td><input type="number" step="0.01" [(ngModel)]="c.end" style="width:6rem" /></td>
            <td><textarea rows="2" [(ngModel)]="c.text"></textarea></td></tr>
        }</tbody>
      </table></div>
      <div class="row" style="margin-top:.5rem">
        <button class="primary" (click)="save()">Save caption edits</button>
        <button (click)="reviewed()">Mark timing reviewed</button>
        <span class="small muted">Edits are validated (no overlaps, ≤2 lines, within the narration).
          Saving makes the render stale.</span>
      </div>
    } @else {
      <p class="muted">No captions yet. Run "Captions" in Overview after the narration exists.</p>
    }
  `,
})
export class CaptionsTab {
  private api = inject(Api);
  jobId = input.required<string>();
  version = input(0);
  changed = output<void>();
  v = signal<CaptionsView | null>(null);
  rows = signal<Caption[]>([]);
  error = signal('');

  constructor() { effect(() => { this.version(); this.load(); }); }

  private set(v: CaptionsView) {
    this.v.set(v);
    this.rows.set((v.track?.captions ?? []).map((c) => ({ ...c })));
  }

  async load() {
    try { this.set(await this.api.captions(this.jobId())); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async save() {
    try {
      const caps = this.rows().map((c) => ({ ...c, start: Number(c.start), end: Number(c.end) }));
      this.set(await this.api.saveCaptions(this.jobId(), caps));
      this.changed.emit();
    } catch (e) { this.error.set(errorText(e)); }
  }

  async reviewed() {
    try { this.set(await this.api.captionsReviewed(this.jobId())); this.changed.emit(); }
    catch (e) { this.error.set(errorText(e)); }
  }
}
